"""Filtro de asserção das entidades do Text Analytics for health (DoD da US13).

| Asserção                                  | Decisão                                 |
| ----------------------------------------- | --------------------------------------- |
| certeza ``negative``/``negative_possible`` | descarta ("nega dor no peito")          |
| associação ``other``                      | descarta ("meu pai teve infarto")       |
| condicionalidade ``hypothetical``/``conditional`` | descarta ("se a falta de ar piorar") |
| temporalidade ``past``                    | mantém com severidade um nível abaixo   |
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from contratos import Severidade

from .normalize import normalize

CERTEZAS_DESCARTADAS = frozenset({"negative", "negative_possible"})
CONDICIONALIDADES_DESCARTADAS = frozenset({"hypothetical", "conditional"})
ASSOCIACAO_DESCARTADA = "other"
TEMPORALIDADE_REBAIXADA = "past"

_ABAIXO = {
    Severidade.ALTA: Severidade.MEDIA,
    Severidade.MEDIA: Severidade.BAIXA,
    Severidade.BAIXA: Severidade.INFO,
    Severidade.INFO: Severidade.INFO,
}

FONTE_SERVICO = "servico"
FONTE_REGRA_LOCAL = "regra_local"


@dataclass(frozen=True)
class AssertionDecision:
    keep: bool
    reason: Optional[str] = None  # motivo do descarte, ex. "certeza=negative"
    downgrade: bool = False


def evaluate_assertion(
    certainty: Optional[str],
    conditionality: Optional[str],
    association: Optional[str],
    temporality: Optional[str],
) -> AssertionDecision:
    """Aplica as regras na ordem da DoD; a primeira que descarta vence."""
    if certainty in CERTEZAS_DESCARTADAS:
        return AssertionDecision(keep=False, reason=f"certeza={certainty}")
    if association == ASSOCIACAO_DESCARTADA:
        return AssertionDecision(keep=False, reason=f"associacao={association}")
    if conditionality in CONDICIONALIDADES_DESCARTADAS:
        return AssertionDecision(keep=False, reason=f"condicionalidade={conditionality}")
    return AssertionDecision(keep=True, downgrade=temporality == TEMPORALIDADE_REBAIXADA)


def downgrade(severidade: Severidade) -> Severidade:
    """alta → media → baixa → info."""
    return _ABAIXO[severidade]


def infer_past(sentenca: str, marcadores: Iterable[re.Pattern[str]]) -> bool:
    """Regra local de temporalidade: a sentença tem marcador de passado em pt-BR.

    Usada só quando o serviço não devolve ``temporality`` (a API GA não devolve).
    """
    texto = normalize(sentenca)
    return any(m.search(texto) for m in marcadores)


def resolve_temporality(
    do_servico: Optional[str], sentenca: str, marcadores: Iterable[re.Pattern[str]],
) -> tuple[Optional[str], Optional[str]]:
    """``(temporalidade, fonte)``: o valor do serviço tem precedência sobre a regra local."""
    if do_servico:
        return do_servico, FONTE_SERVICO
    if infer_past(sentenca, marcadores):
        return TEMPORALIDADE_REBAIXADA, FONTE_REGRA_LOCAL
    return None, None
