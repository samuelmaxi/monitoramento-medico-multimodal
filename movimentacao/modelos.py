"""Tipos da série de movimentação: o que cada fonte observa e o que sai dela."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from contratos import Severidade


class Posicao(StrEnum):
    SUPINO = "supino"
    LATERAL_ESQ = "lateral_esquerda"
    LATERAL_DIR = "lateral_direita"
    PRONO = "prono"
    SENTADO = "sentado"
    ROTATIVO = "rotativo"
    """Cama terapêutica em rotação contínua: o paciente é reposicionado pela cama."""
    FORA_DO_LEITO = "fora_do_leito"
    CHAO = "chao"
    DESCONHECIDA = "desconhecida"


NO_LEITO = frozenset(
    {Posicao.SUPINO, Posicao.LATERAL_ESQ, Posicao.LATERAL_DIR, Posicao.PRONO, Posicao.SENTADO}
)
"""Posições que contam para o relógio de imobilidade."""

Periodo = Literal["dia", "noite"]
TipoMarco = Literal["mudanca_posicao", "saida_leito", "retorno_leito", "queda"]


@dataclass(frozen=True)
class Observacao:
    """Um registro de posição num instante, de uma fonte.

    ``mudanca=True`` marca um reposicionamento registrado sem posição nova
    (ex.: ``Turn = Side to Side`` na enfermagem). ``queda=True`` vem de um
    detector de queda, independentemente da posição.
    """

    instante: datetime
    posicao: Posicao
    fonte: str
    queda: bool = False
    mudanca: bool = False
    evidencia: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class Marco:
    tipo: TipoMarco
    instante: datetime
    periodo: Periodo
    de: Posicao
    para: Posicao
    fonte: str


@dataclass(frozen=True)
class Anomalia:
    event_type: str
    inicio: datetime
    fim: datetime
    severidade: Severidade
    score: float
    resumo: str
    features: dict[str, Any]
    fontes: tuple[str, ...]
    thresholds: dict[str, Any] = field(default_factory=dict)
