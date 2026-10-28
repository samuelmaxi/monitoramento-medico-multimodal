"""Tradução de :class:`~video.desvios.modelos.Desvio` para o contrato da US04.

Os quatro ``event_type`` já existem no catálogo da modalidade ``video``
(``postura_fora_da_faixa``, ``assimetria_movimento``, ``mudanca_brusca_ou_queda``,
``presenca_indevida_area_critica``), então nada muda no schema.

=========================  ==============================================
Desvio                     ``EventoAchado``
=========================  ==============================================
início/fim do trecho       ``window`` e ``timestamp`` (= início)
intensidade do desvio      ``score`` (0,5 no limiar, 1,0 na saturação)
regra do perfil            ``severity`` (declarada no YAML)
medida, valor, quadro      ``evidence.features``
limiar, janela, versão     ``evidence.thresholds`` (com ``versao_limiares``)
eventos da US07            ``related_event_ids``
quadro de evidência        ``evidence.artifacts`` (``kind="frame"``)
=========================  ==============================================
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from contratos import (
    SEM_ACHADOS,
    Artefato,
    Contexto,
    EventoAchado,
    Evidencia,
    Janela,
    Modalidade,
    Severidade,
)

from . import MODEL_VERSION
from .config import ConfigDesvios, Perfil
from .modelos import Desvio, ResultadoAnalise


@dataclass(frozen=True)
class ContextoEmissao:
    """O que o evento precisa além do desvio: paciente, relógio e origem."""

    patient_id: str
    inicio_utc: datetime
    """Instante do quadro 0 do vídeo; somado ao tempo relativo de cada desvio."""
    contexto: Contexto | None = None
    modelos: Mapping[str, str] = field(default_factory=dict)
    ingested_at: datetime | None = None


def _round(valor: Any) -> Any:
    return round(valor, 3) if isinstance(valor, float) else valor


def montar_evento(
    desvio: Desvio,
    ctx: ContextoEmissao,
    *,
    perfil: Perfil,
    cfg: ConfigDesvios,
    artefatos: Sequence[Artefato] = (),
) -> EventoAchado:
    inicio = desvio.inicio_utc or ctx.inicio_utc + timedelta(seconds=desvio.inicio_s)
    fim = desvio.fim_utc or ctx.inicio_utc + timedelta(seconds=desvio.fim_s)
    features: dict[str, Any] = {
        "regra": desvio.regra,
        "medida": desvio.medida,
        "valor": desvio.valor,
        "unidade": desvio.unidade,
        "inicio_video_s": round(desvio.inicio_s, 3),
        "fim_video_s": round(desvio.fim_s, 3),
        "pico_video_s": round(desvio.pico_s, 3),
        "duracao_s": round(desvio.duracao_s, 3),
        "frame_index": desvio.frame_idx,
        "perfil": perfil.id,
        "tipo_video": perfil.tipo_video,
        **{chave: _round(valor) for chave, valor in desvio.features.items()},
    }
    return EventoAchado.criar(
        patient_id=ctx.patient_id,
        modality=Modalidade.VIDEO,
        event_type=desvio.event_type,
        timestamp=inicio,
        window=Janela(start=inicio, end=fim),
        ingested_at=ctx.ingested_at,
        score=desvio.score,
        severity=desvio.severidade,
        evidence=Evidencia(
            summary=desvio.resumo,
            features=features,
            thresholds={
                "versao_limiares": cfg.versao,
                "perfil": perfil.id,
                "limiar": desvio.limiar,
                "janela_minima_s": desvio.janela_s,
                "lacuna_max_s": cfg.geral.lacuna_max_s,
            },
            models=dict(ctx.modelos),
            artifacts=list(artefatos),
        ),
        model_version=MODEL_VERSION,
        context=ctx.contexto,
        related_event_ids=list(desvio.relacionados),
    )


def montar_sem_achados(
    resultado: ResultadoAnalise,
    ctx: ContextoEmissao,
    *,
    perfil: Perfil,
    cfg: ConfigDesvios,
) -> EventoAchado:
    """``sem_achados`` (info): o vídeo foi analisado e ficou dentro do padrão.

    Só faz sentido com análise confiável; com pouca pessoa visível, dizer à
    fusão que o vídeo está normal seria falso.
    """
    if not resultado.confiavel:
        raise ValueError("análise pouco confiável não gera sem_achados")
    duracao = resultado.duracao_s or 0.0
    features: dict[str, Any] = {
        "perfil": perfil.id,
        "tipo_video": perfil.tipo_video,
        "quadros_analisados": resultado.quadros,
        "duracao_s": round(duracao, 2),
        "cobertura_pct": resultado.cobertura_pct,
    }
    frase = f"Vídeo analisado com o perfil '{perfil.id}' sem desvios do padrão esperado"
    if resultado.repeticoes is not None:
        features["repeticoes_contadas"] = resultado.repeticoes.contadas
        frase += f" ({resultado.repeticoes.contadas} repetições, todas no alvo)"
    return EventoAchado.criar(
        patient_id=ctx.patient_id,
        modality=Modalidade.VIDEO,
        event_type=SEM_ACHADOS,
        timestamp=ctx.inicio_utc,
        window=Janela(start=ctx.inicio_utc, end=ctx.inicio_utc + timedelta(seconds=duracao)),
        ingested_at=ctx.ingested_at,
        score=0.0,
        severity=Severidade.INFO,
        evidence=Evidencia(
            summary=frase + ".",
            features=features,
            thresholds={"versao_limiares": cfg.versao, "perfil": perfil.id},
            models=dict(ctx.modelos),
        ),
        model_version=MODEL_VERSION,
        context=ctx.contexto,
    )


def relatorio(
    resultado: ResultadoAnalise,
    eventos: Sequence[EventoAchado],
    *,
    perfil: Perfil,
    cfg: ConfigDesvios,
    fonte: Mapping[str, Any],
) -> dict[str, Any]:
    """Relatório do vídeo: padrão aplicado, desvios com timestamp e evidência."""
    desvios = [e for e in eventos if e.event_type != SEM_ACHADOS]
    return {
        "fonte": dict(fonte),
        "perfil": perfil.id,
        "tipo_video": perfil.tipo_video,
        "padrao_esperado": perfil.descricao,
        "versao_limiares": cfg.versao,
        "config": str(cfg.fonte) if cfg.fonte else None,
        "model_version": MODEL_VERSION,
        "duracao_s": resultado.duracao_s,
        "quadros_analisados": resultado.quadros,
        "cobertura_pct": resultado.cobertura_pct,
        "confiavel": resultado.confiavel,
        "avisos": list(resultado.avisos),
        "repeticoes": resultado.repeticoes.para_dict() if resultado.repeticoes else None,
        "total_desvios": len(desvios),
        "desvios_por_tipo": dict(Counter(e.event_type for e in desvios)),
        "desvios": [
            {
                "event_id": str(e.event_id),
                "event_type": e.event_type,
                "severity": e.severity.value,
                "score": e.score,
                "timestamp": e.para_dict()["timestamp"],
                "inicio_video_s": e.evidence.features["inicio_video_s"],
                "fim_video_s": e.evidence.features["fim_video_s"],
                "pico_video_s": e.evidence.features["pico_video_s"],
                "frame_index": e.evidence.features["frame_index"],
                "resumo": e.evidence.summary,
                "evidencia": [a.uri for a in e.evidence.artifacts],
            }
            for e in desvios
        ],
    }
