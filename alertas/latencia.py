"""Latência do alerta: do dado bruto (``ingested_at``) até o primeiro envio (``sent_at``).

Quando o achado não traz ``ingested_at``, a base vira ``detected_at`` e o alerta
fica marcado em ``latency_base``, para o relatório não misturar as duas medidas.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

from .modelos import Alerta

META_P95_S = 10.0  # meta do DoD da US19: p95 da ingestão ao envio


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


def resumo_latencia(alertas: list[Alerta], meta_p95_s: float = META_P95_S) -> dict:
    """Estatísticas da latência dos alertas já enviados. ``atendida`` é ``None`` sem amostras."""
    medidas = latencias_segundos(alertas)
    bases = dict(Counter(a.latency_base for a in alertas if a.sent_at is not None))
    if not medidas:
        return {"amostras": 0, "bases": bases, "meta_p95_s": meta_p95_s,
                "p50_s": None, "p95_s": None, "max_s": None, "atendida": None}
    p95 = percentil(medidas, 95)
    return {
        "amostras": len(medidas),
        "bases": bases,
        "meta_p95_s": meta_p95_s,
        "p50_s": round(percentil(medidas, 50), 3),
        "p95_s": round(p95, 3),
        "max_s": round(max(medidas), 3),
        "atendida": p95 <= meta_p95_s,
    }


def ler_alertas(caminho: str | Path) -> list[Alerta]:
    """Lê o último estado de cada alerta do ``alertas.jsonl``."""
    atuais: dict[str, Alerta] = {}
    for linha in Path(caminho).read_text(encoding="utf-8").splitlines():
        if linha.strip():
            alerta = Alerta.model_validate(json.loads(linha))
            atuais[str(alerta.id)] = alerta
    return list(atuais.values())
