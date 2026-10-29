"""Tradução de :class:`~movimentacao.modelos.Anomalia` para o contrato da US04.

Os cinco ``event_type`` já existem no catálogo da modalidade ``movimentacao``,
então nada muda no schema.

==========================  ===================================================
Anomalia                    ``EventoAchado``
==========================  ===================================================
início/fim                  ``window``; ``timestamp`` = fim (quando o achado
                            ficou completo, como no exemplo do contrato)
regra e severidade do YAML  ``severity`` e ``score``
métricas e medidas          ``evidence.features`` (+ ``fontes``)
limiares e versão           ``evidence.thresholds`` (com ``versao_limiares``)
paciente / internação       ``patient_id`` / ``context.encounter_id``
                            pseudonimizados (HMAC com ``PSEUDONYM_KEY``)
==========================  ===================================================
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
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
from .config import ConfigMovimentacao
from .modelos import Anomalia, Observacao
from .serie import metricas, ordenar

_RE_INTERNACAO = re.compile(PADRAO_INTERNACAO)


def _paciente(paciente: str, chave: bytes | str | None) -> str:
    """ID de origem → pseudônimo; um ``pt_…`` passa como está."""
    if e_pseudonimo_de_paciente(paciente):
        return paciente
    return pseudonimizar_paciente(paciente, chave=chave)


def _contexto(
    internacao: str | None,
    bed_id: str | None,
    source_id: str | None,
    chave: bytes | str | None,
) -> Contexto | None:
    if not (internacao or bed_id or source_id):
        return None
    encounter_id = None
    if internacao:
        encounter_id = (
            internacao
            if _RE_INTERNACAO.fullmatch(internacao)
            else pseudonimizar_internacao(internacao, chave=chave)
        )
    return Contexto(encounter_id=encounter_id, bed_id=bed_id, source_id=source_id)


def _valor(valor: Any) -> Any:
    if isinstance(valor, float):
        return round(valor, 4)
    if isinstance(valor, str):
        return valor[:200]
    return valor


def montar_evento(
    anomalia: Anomalia,
    cfg: ConfigMovimentacao,
    *,
    paciente: str,
    internacao: str | None = None,
    bed_id: str | None = None,
    source_id: str | None = None,
    chave: bytes | str | None = None,
    sintetico: bool = False,
) -> EventoAchado:
    features = {k: _valor(v) for k, v in anomalia.features.items()}
    features["fontes"] = "+".join(anomalia.fontes)
    return EventoAchado.criar(
        patient_id=_paciente(paciente, chave),
        modality=Modalidade.MOVIMENTACAO,
        event_type=anomalia.event_type,
        timestamp=anomalia.fim,
        window=Janela(start=anomalia.inicio, end=anomalia.fim),
        score=anomalia.score,
        severity=anomalia.severidade,
        evidence=Evidencia(
            summary=anomalia.resumo,
            features=features,
            thresholds={**anomalia.thresholds, "versao_limiares": cfg.versao},
        ),
        model_version=MODEL_VERSION,
        context=_contexto(internacao, bed_id, source_id, chave),
        is_synthetic=sintetico,
    )


def montar_sem_achados(
    observacoes: Sequence[Observacao],
    cfg: ConfigMovimentacao,
    *,
    paciente: str,
    internacao: str | None = None,
    bed_id: str | None = None,
    source_id: str | None = None,
    chave: bytes | str | None = None,
) -> EventoAchado:
    """``sem_achados`` (info): a série foi analisada e ficou dentro do padrão.

    Sem observação não há o que afirmar, então isso é recusado.
    """
    serie = ordenar(observacoes)
    if not serie:
        raise ValueError("sem nenhuma observação não dá para afirmar sem_achados")
    inicio, fim = serie[0].instante, serie[-1].instante
    m = metricas(serie, cfg)
    return EventoAchado.criar(
        patient_id=_paciente(paciente, chave),
        modality=Modalidade.MOVIMENTACAO,
        event_type=SEM_ACHADOS,
        timestamp=fim,
        window=Janela(start=inicio, end=fim),
        score=0.0,
        severity=Severidade.INFO,
        evidence=Evidencia(
            summary=(
                f"Movimentação dentro do padrão em {m['horas_observadas']:g} h observadas "
                f"({m['reposicionamentos_por_hora']:g} reposicionamentos/h)"
            ),
            features={k: _valor(v) for k, v in m.items()},
            thresholds={
                "versao_limiares": cfg.versao,
                "limiar_imobilidade_min": cfg.imobilidade.limiar_min,
            },
        ),
        model_version=MODEL_VERSION,
        context=_contexto(internacao, bed_id, source_id, chave),
    )


def relatorio(
    observacoes: Sequence[Observacao],
    anomalias: Sequence[Anomalia],
    cfg: ConfigMovimentacao,
) -> dict[str, Any]:
    """Resumo legível de uma série: métricas, fontes e achados por tipo/severidade."""
    return {
        "model_version": MODEL_VERSION,
        "versao_limiares": cfg.versao,
        "observacoes": len(observacoes),
        "fontes": dict(Counter(o.fonte for o in observacoes)),
        "metricas": metricas(observacoes, cfg),
        "eventos": dict(Counter(a.event_type for a in anomalias)),
        "por_severidade": dict(Counter(a.severidade.value for a in anomalias)),
    }


__all__ = ["montar_evento", "montar_sem_achados", "relatorio"]
