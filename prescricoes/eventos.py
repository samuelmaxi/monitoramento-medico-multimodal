"""Tradução de :class:`~prescricoes.modelos.Anomalia` para o contrato da US04.

Os quatro ``event_type`` já existem no catálogo da modalidade ``prescricoes``,
então nada muda no schema.

==========================  =============================================
Anomalia                    ``EventoAchado``
==========================  =============================================
início da ordem nova        ``timestamp`` (e ``window`` com o período)
regra e severidade do YAML  ``severity``; ``score`` 0,5 no limiar → 1,0
antes × depois              ``evidence.features`` (``antes_*``, ``depois_*``)
justificativa legível       ``evidence.summary``
limiares e versão           ``evidence.thresholds`` (com ``versao_limiares``)
``subject_id``/``hadm_id``  ``patient_id`` / ``context.encounter_id`` pseudonimizados
==========================  =============================================
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from contratos import (
    SEM_ACHADOS,
    Contexto,
    EventoAchado,
    Evidencia,
    Janela,
    Modalidade,
    Severidade,
    e_pseudonimo_de_paciente,
    pseudonimizar_internacao,
    pseudonimizar_paciente,
)

from contratos.pseudonimizacao import PADRAO_INTERNACAO

from . import MODEL_VERSION
from .config import ConfigPrescricoes
from .modelos import Anomalia, Internacao

_RE_INTERNACAO = re.compile(PADRAO_INTERNACAO)
_CAMPOS_ORDEM = ("medicamento", "dose", "unidade", "dose_original", "via", "frequencia", "dose_diaria", "inicio", "fim")


def _valor(valor: Any) -> Any:
    if isinstance(valor, float):
        return round(valor, 4)
    if isinstance(valor, str):
        return valor[:200]
    return valor


def _ordem(prefixo: str, dados: Mapping[str, Any] | None) -> dict[str, Any]:
    if dados is None:
        return {}
    return {f"{prefixo}_{campo}": _valor(dados.get(campo)) for campo in _CAMPOS_ORDEM}


def _paciente(paciente: str, chave: bytes | str | None) -> str:
    """ID de origem (``subject_id``) → pseudônimo; um ``pt_…`` passa como está."""
    if e_pseudonimo_de_paciente(paciente):
        return paciente
    return pseudonimizar_paciente(paciente, chave=chave)


def _contexto(internacao: str | None, chave: bytes | str | None) -> Contexto | None:
    if not internacao:
        return None
    if _RE_INTERNACAO.fullmatch(internacao):
        return Contexto(encounter_id=internacao)
    return Contexto(encounter_id=pseudonimizar_internacao(internacao, chave=chave))


def montar_evento(
    anomalia: Anomalia,
    cfg: ConfigPrescricoes,
    *,
    sintetica: bool = False,
    ingested_at: datetime | None = None,
    chave: bytes | str | None = None,
) -> EventoAchado:
    features: dict[str, Any] = {
        "regra": anomalia.regra,
        "principio_ativo": anomalia.principio[:200],
        **{k: _valor(v) for k, v in anomalia.features.items()},
        **_ordem("antes", anomalia.antes),
        **_ordem("depois", anomalia.depois),
        "ordens": ",".join(anomalia.ordens)[:200],
    }
    return EventoAchado.criar(
        patient_id=_paciente(anomalia.paciente, chave),
        modality=Modalidade.PRESCRICOES,
        event_type=anomalia.event_type,
        timestamp=anomalia.instante,
        window=Janela(
            start=min(anomalia.inicio, anomalia.instante), end=max(anomalia.fim, anomalia.instante)
        ),
        ingested_at=ingested_at,
        score=anomalia.score,
        severity=anomalia.severidade,
        evidence=Evidencia(
            summary=anomalia.resumo[:500],
            features=features,
            thresholds={"versao_limiares": cfg.versao, **dict(anomalia.limiares)},
        ),
        model_version=MODEL_VERSION,
        context=_contexto(anomalia.internacao, chave),
        is_synthetic=sintetica,
    )


def montar_sem_achados(
    internacao: Internacao,
    prescricoes_analisadas: int,
    cfg: ConfigPrescricoes,
    *,
    sintetica: bool = False,
    chave: bytes | str | None = None,
) -> EventoAchado:
    """``sem_achados`` (info): a internação foi analisada e nada destoou."""
    fim = internacao.alta or internacao.admissao
    return EventoAchado.criar(
        patient_id=_paciente(internacao.paciente, chave),
        modality=Modalidade.PRESCRICOES,
        event_type=SEM_ACHADOS,
        timestamp=internacao.admissao,
        window=Janela(start=internacao.admissao, end=max(fim, internacao.admissao)),
        score=0.0,
        severity=Severidade.INFO,
        evidence=Evidencia(
            summary=(
                f"{prescricoes_analisadas} prescrições da internação analisadas, sem mudança "
                "abrupta de dose, duplicidade, suspensão abrupta ou medicamento de alta vigilância."
            ),
            features={"prescricoes_analisadas": prescricoes_analisadas},
            thresholds={"versao_limiares": cfg.versao},
        ),
        model_version=MODEL_VERSION,
        context=_contexto(internacao.id, chave),
        is_synthetic=sintetica,
    )


def relatorio(
    anomalias: Sequence[Anomalia],
    eventos: Sequence[EventoAchado],
    cfg: ConfigPrescricoes,
    *,
    fonte: Mapping[str, Any],
    prescricoes: int,
    internacoes: Iterable[str],
) -> dict[str, Any]:
    """Resumo da execução: volume por tipo/severidade e cada anomalia com a justificativa."""
    internacoes = list(internacoes)
    com_anomalia = {a.internacao for a in anomalias}
    return {
        "fonte": dict(fonte),
        "versao_limiares": cfg.versao,
        "config": str(cfg.fonte) if cfg.fonte else None,
        "model_version": MODEL_VERSION,
        "lista_alta_vigilancia": cfg.alta_vigilancia.lista,
        "prescricoes_analisadas": prescricoes,
        "internacoes": len(internacoes),
        "internacoes_com_anomalia": len(com_anomalia & set(internacoes)),
        "total_anomalias": len(anomalias),
        "por_tipo": dict(Counter(a.event_type for a in anomalias)),
        "por_tipo_e_severidade": {
            f"{t}/{s}": n
            for (t, s), n in sorted(Counter((a.event_type, a.severidade.value) for a in anomalias).items())
        },
        "alertaveis": sum(e.alertavel for e in eventos if e.event_type != SEM_ACHADOS),
        "anomalias": [
            {
                "event_id": str(e.event_id),
                "event_type": a.event_type,
                "regra": a.regra,
                "severity": a.severidade.value,
                "score": a.score,
                "timestamp": e.para_dict()["timestamp"],
                "patient_id": e.patient_id,
                "principio_ativo": a.principio,
                "justificativa": a.resumo,
                "antes": dict(a.antes) if a.antes else None,
                "depois": dict(a.depois) if a.depois else None,
            }
            for a, e in zip(
                anomalias, [e for e in eventos if e.event_type != SEM_ACHADOS], strict=True
            )
        ],
    }
