"""Latência do alerta: do dado bruto (``ingested_at``) até o primeiro envio (``sent_at``).

Quando o achado não traz ``ingested_at``, a base vira ``detected_at`` e o alerta
fica marcado em ``latency_base``, para o relatório não misturar as duas medidas.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .modelos import Alerta


def latencias_segundos(alertas: list[Alerta]) -> list[float]:
    medidas = []
    for alerta in alertas:
        if alerta.sent_at is None:
            continue
        base = alerta.ingested_at or alerta.detected_at
        medidas.append((alerta.sent_at - base).total_seconds())
    return medidas


def percentil(valores: list[float], p: float) -> float:
    """Percentil pelo método nearest-rank. ``p`` vai de 0 a 100."""
    if not valores:
        raise ValueError("sem valores para calcular percentil")
    ordenados = sorted(valores)
    posicao = max(math.ceil(p / 100 * len(ordenados)) - 1, 0)
    return ordenados[posicao]


def ler_alertas(caminho: str | Path) -> list[Alerta]:
    """Lê o último estado de cada alerta do ``alertas.jsonl``."""
    atuais: dict[str, Alerta] = {}
    for linha in Path(caminho).read_text(encoding="utf-8").splitlines():
        if linha.strip():
            alerta = Alerta.model_validate(json.loads(linha))
            atuais[str(alerta.id)] = alerta
    return list(atuais.values())
