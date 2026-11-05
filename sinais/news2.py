"""Camada de regras clínicas: NEWS2 (Royal College of Physicians, 2017), SpO2 escala 1."""

from __future__ import annotations

import math
from dataclasses import dataclass

# (limite superior inclusivo, pontos), na ordem da tabela de referência da US15.
TABELA = {
    "fr": ((8, 3), (11, 1), (20, 0), (24, 2), (math.inf, 3)),
    "spo2": ((91, 3), (93, 2), (95, 1), (math.inf, 0)),
    "pas": ((90, 3), (100, 2), (110, 1), (219, 0), (math.inf, 3)),
    "fc": ((40, 3), (50, 1), (90, 0), (110, 1), (130, 2), (math.inf, 3)),
    "temp": ((35.0, 3), (36.0, 1), (38.0, 0), (39.0, 1), (math.inf, 2)),
}
PARAMETROS = (*TABELA, "o2_suplementar", "alerta")

URGENTE_TOTAL = 5
EMERGENCIA_TOTAL = 7
PARAMETRO_UNICO = 3
TOTAL_MAXIMO = 20

URGENTE = "news2_resposta_urgente"
EMERGENCIA = "news2_emergencia"


def pontos_parametro(parametro: str, valor: float) -> int:
    """Pontos de um parâmetro. ``o2_suplementar`` e ``alerta`` são 0/1."""
    if parametro == "o2_suplementar":
        return 2 if valor else 0
    if parametro == "alerta":
        return 0 if valor else 3  # C, V, P ou U
    return next(pontos for limite, pontos in TABELA[parametro] if valor <= limite)


@dataclass(frozen=True)
class ResultadoNews2:
    pontos: dict[str, int]
    ausentes: tuple[str, ...]

    @property
    def total(self) -> int:
        return sum(self.pontos.values())

    @property
    def gatilho(self) -> str | None:
        if self.total >= EMERGENCIA_TOTAL:
            return EMERGENCIA
        if self.total >= URGENTE_TOTAL or max(self.pontos.values(), default=0) >= PARAMETRO_UNICO:
            return URGENTE
        return None


def calcular(valores: dict[str, float]) -> ResultadoNews2:
    """NEWS2 por parâmetro e total. Parâmetro ausente não pontua e fica em ``ausentes``."""
    pontos = {p: pontos_parametro(p, valores[p]) for p in PARAMETROS if valores.get(p) is not None}
    return ResultadoNews2(pontos, tuple(p for p in PARAMETROS if p not in pontos))
