"""Limpeza de artefatos e dados faltantes (ver docs/arquitetura/us15_sinais_vitais.md)."""

from __future__ import annotations

import math
from datetime import timedelta

# Faixa fisiologicamente possível (inclusiva). Fora dela é artefato de sensor/digitação: descarta.
FAIXAS = {
    "fc": (20, 300),
    "pas": (40, 300),
    "pad": (15, 200),
    "spo2": (50, 100),
    "fr": (3, 70),
    "temp": (25.0, 45.0),
    "o2_suplementar": (0, 1),
    "alerta": (0, 1),
}

# Por quanto tempo o último valor válido ainda representa o paciente (forward-fill limitado).
# Depois disso o parâmetro é ausente: não pontua no NEWS2 e o evento sai com news2_parcial.
VALIDADE = {
    "fc": timedelta(hours=2),
    "pas": timedelta(hours=2),
    "pad": timedelta(hours=2),
    "spo2": timedelta(hours=2),
    "fr": timedelta(hours=2),
    "temp": timedelta(hours=6),
    "o2_suplementar": timedelta(hours=8),
    "alerta": timedelta(hours=8),
}


def plausivel(sinal: str, valor: float | None) -> bool:
    if valor is None or not math.isfinite(valor):
        return False
    minimo, maximo = FAIXAS[sinal]
    return minimo <= valor <= maximo
