"""Anomalias na evolução de prescrições (US16), pelo mesmo caminho do terminal.

A análise é síncrona e local (regras, sem serviço externo): uma internação
inteira leva milissegundos. Os eventos também são publicados no barramento
local (``EVENTOS_JSONL``), como nas outras rotas.

- ``POST /prescricoes/analise``: as ordens de uma internação no corpo.
- ``GET /prescricoes/mimic/{subject_id}``: um paciente do MIMIC-IV Demo baixado
  com ``python -m prescricoes.run --baixar`` (pasta em ``PRESCRICOES_DADOS_DIR``).
- ``GET /prescricoes/config``: limiares e lista ISMP em uso.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from app.schemas import (
    AnaliseMimicResponse,
    AnalisePrescricoesRequest,
    AnalisePrescricoesResponse,
    ConfigPrescricoesResponse,
    InternacaoAnalisada,
    PrescricaoNormalizada,
)
from contratos import BarramentoLocal, ChavePseudonimizacaoAusente, EventoAchado, pseudonimizar_internacao
from prescricoes import MODEL_VERSION
from prescricoes.config import CAMINHO_PADRAO, ConfigPrescricoes, ErroDeConfiguracao, carregar_config
from prescricoes.entrada import criar_prescricao
from prescricoes.eventos import montar_evento, montar_sem_achados
from prescricoes.mimic import TABELAS, carregar_internacoes, carregar_prescricoes
from prescricoes.modelos import Anomalia, Internacao, Prescricao
from prescricoes.regras import detectar

router = APIRouter(prefix="/prescricoes", tags=["US16 · prescrições"])


@lru_cache
def get_config() -> ConfigPrescricoes:
    return carregar_config(os.getenv("PRESCRICOES_CONFIG_PATH", str(CAMINHO_PADRAO)))


@lru_cache
def get_barramento() -> BarramentoLocal:
    return BarramentoLocal.do_ambiente()


def get_dados_dir() -> Path:
    return Path(os.getenv("PRESCRICOES_DADOS_DIR", "dados/mimic-iv-demo"))


@lru_cache
def _mimic(diretorio: Path) -> tuple[dict[str, list[Prescricao]], dict[str, Internacao]]:
    por_paciente: dict[str, list[Prescricao]] = {}
    for p in carregar_prescricoes(diretorio):
        por_paciente.setdefault(p.paciente, []).append(p)
    return por_paciente, carregar_internacoes(diretorio)


def _config() -> ConfigPrescricoes:
    try:
        return get_config()
    except (OSError, ErroDeConfiguracao) as exc:
        raise HTTPException(status_code=503, detail=f"Configuração da US16 indisponível: {exc}") from exc


def _eventos(
    prescricoes: Sequence[Prescricao],
    internacoes: dict[str | None, Internacao],
    cfg: ConfigPrescricoes,
    *,
    sintetica: bool = False,
) -> tuple[list[Anomalia], list[EventoAchado]]:
    """Uma anomalia por evento; internação sem nenhuma vira um ``sem_achados``."""
    anomalias = detectar(prescricoes, cfg, internacoes)
    sinteticas = {p.ordem_id for p in prescricoes if p.sintetica}
    try:
        eventos = [
            montar_evento(a, cfg, sintetica=sintetica or bool(sinteticas.intersection(a.ordens)))
            for a in anomalias
        ]
        com_anomalia = {a.internacao for a in anomalias}
        for hadm, internacao in internacoes.items():
            if hadm not in com_anomalia:
                n = sum(p.internacao == hadm for p in prescricoes)
                eventos.append(montar_sem_achados(internacao, n, cfg, sintetica=sintetica))
    except ChavePseudonimizacaoAusente as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return anomalias, eventos


def _publicar(barramento: BarramentoLocal, eventos: list[EventoAchado], publicar: bool) -> None:
    if publicar:
        barramento.emitir_varios(eventos)


def _por_tipo(eventos: list[EventoAchado]) -> dict[str, int]:
    return dict(Counter(e.event_type for e in eventos if e.event_type != "sem_achados"))


@router.post("/analise", response_model=AnalisePrescricoesResponse, response_model_exclude_none=True)
def analisar_prescricoes(
    payload: AnalisePrescricoesRequest,
    detalhes: bool = Query(False, description="Inclui cada ordem normalizada (princípio, unidade, dose diária)"),
    publicar: bool = Query(True, description="Publica os eventos no barramento local"),
    barramento: BarramentoLocal = Depends(get_barramento),
) -> AnalisePrescricoesResponse:
    cfg = _config()
    try:
        prescricoes = [
            criar_prescricao(
                ordem_id=item.ordem_id or f"ordem-{i}",
                paciente=payload.patient_id,
                internacao=payload.encounter_id,
                medicamento=item.medicamento,
                dose=item.dose,
                unidade=item.unidade,
                via=item.via,
                frequencia=item.frequencia,
                doses_24h=item.doses_24h,
                inicio=item.inicio,
                fim=item.fim,
                apresentacao=item.apresentacao,
                status=item.status,
                escala_movel=item.escala_movel,
                sintetica=payload.is_synthetic,
            )
            for i, item in enumerate(payload.prescricoes, start=1)
        ]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ids = [p.ordem_id for p in prescricoes]
    if len(set(ids)) != len(ids):
        repetidos = sorted({i for i in ids if ids.count(i) > 1})
        raise HTTPException(status_code=422, detail=f"ordem_id repetido: {', '.join(repetidos)}")
    admissao = payload.admissao or min(p.inicio for p in prescricoes)
    if payload.alta is not None and payload.alta < admissao:
        raise HTTPException(status_code=422, detail="alta antes da admissão")
    internacao = Internacao(
        id=payload.encounter_id, paciente=payload.patient_id, admissao=admissao,
        alta=payload.alta, obito=payload.obito,
    )
    _, eventos = _eventos(prescricoes, {payload.encounter_id: internacao}, cfg, sintetica=payload.is_synthetic)
    _publicar(barramento, eventos, publicar)
    primeiro = eventos[0]
    return AnalisePrescricoesResponse(
        patient_id=primeiro.patient_id,
        encounter_id=primeiro.context.encounter_id if primeiro.context else None,
        versao_limiares=cfg.versao,
        model_version=MODEL_VERSION,
        prescricoes_analisadas=len(prescricoes),
        total_anomalias=sum(e.event_type != "sem_achados" for e in eventos),
        por_tipo=_por_tipo(eventos),
        eventos=eventos,
        normalizadas=[
            PrescricaoNormalizada(
                ordem_id=p.ordem_id,
                medicamento=p.medicamento,
                principios_ativos=list(p.principios),
                dose=None if p.dose is None else p.dose.valor,
                unidade=None if p.dose is None else p.dose.unidade,
                dose_diaria=p.dose_diaria,
                via_grupo=p.via_grupo,
                tipo_frequencia=p.tipo_frequencia,
            )
            for p in prescricoes
        ]
        if detalhes
        else None,
    )


@router.get("/mimic/{subject_id}", response_model=AnaliseMimicResponse, response_model_exclude_none=True)
def analisar_paciente_mimic(
    subject_id: str,
    hadm_id: str | None = Query(None, description="Só esta internação do paciente"),
    publicar: bool = Query(True, description="Publica os eventos no barramento local"),
    barramento: BarramentoLocal = Depends(get_barramento),
    diretorio: Path = Depends(get_dados_dir),
) -> AnaliseMimicResponse:
    cfg = _config()
    faltando = [t for t in TABELAS if not (diretorio / f"{t}.csv.gz").is_file()]
    if faltando:
        raise HTTPException(
            status_code=503,
            detail=f"MIMIC-IV Demo ausente em {diretorio} ({', '.join(faltando)}). "
            "Rode: python -m prescricoes.run --baixar",
        )
    por_paciente, internacoes = _mimic(diretorio)
    prescricoes = por_paciente.get(subject_id)
    if not prescricoes:
        raise HTTPException(status_code=404, detail=f"subject_id {subject_id} não está no MIMIC-IV Demo")
    if hadm_id is not None:
        prescricoes = [p for p in prescricoes if p.internacao == hadm_id]
        if not prescricoes:
            raise HTTPException(status_code=404, detail=f"hadm_id {hadm_id} não é do paciente {subject_id}")
    hadms = sorted({p.internacao for p in prescricoes if p.internacao in internacoes})
    do_paciente = {h: internacoes[h] for h in hadms}
    anomalias, eventos = _eventos(prescricoes, do_paciente, cfg)
    _publicar(barramento, eventos, publicar)
    return AnaliseMimicResponse(
        patient_id=eventos[0].patient_id,
        internacoes=[
            InternacaoAnalisada(
                encounter_id=pseudonimizar_internacao(h),
                admissao=do_paciente[h].admissao,
                alta=do_paciente[h].alta,
                prescricoes_analisadas=sum(p.internacao == h for p in prescricoes),
                total_anomalias=sum(a.internacao == h for a in anomalias),
            )
            for h in hadms
        ],
        versao_limiares=cfg.versao,
        model_version=MODEL_VERSION,
        total_anomalias=len(anomalias),
        por_tipo=_por_tipo(eventos),
        eventos=eventos,
    )


@router.get("/config", response_model=ConfigPrescricoesResponse)
def config_em_uso() -> ConfigPrescricoesResponse:
    """Limiares e lista ISMP que a análise usa (``config/us16_prescricoes.yaml``)."""
    cfg = _config()
    return ConfigPrescricoesResponse(
        versao_limiares=cfg.versao,
        model_version=MODEL_VERSION,
        mudanca_abrupta_dose={
            "variacao_min_pct": cfg.dose.variacao_min_pct,
            "janela_h": cfg.dose.janela_h,
            "frequencias_comparaveis": sorted(cfg.dose.frequencias_comparaveis),
        },
        duplicidade_terapeutica={
            "sobreposicao_min_h": cfg.duplicidade.sobreposicao_min_h,
            "frequencias_ignoradas": sorted(cfg.duplicidade.frequencias_ignoradas),
            "principios_permitidos": sorted(cfg.duplicidade.principios_permitidos),
        },
        suspensao_abrupta={
            "classes": list(cfg.suspensao.classes),
            "uso_continuo_min_h": cfg.suspensao.uso_continuo_min_h,
            "sem_retorno_h": cfg.suspensao.sem_retorno_h,
            "desmame_reducao_min_pct": cfg.suspensao.desmame_reducao_min_pct,
        },
        alta_vigilancia={
            "lista": cfg.alta_vigilancia.lista,
            "classes": {
                c.id: {
                    "severidade": c.severidade.value,
                    "principios": sorted(c.principios),
                    **({"vias": sorted(c.vias)} if c.vias else {}),
                }
                for c in cfg.alta_vigilancia.classes
            },
        },
    )
