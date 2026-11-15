"""Operações de sinal compartilhadas: RMS por quadro, nível em dB e trechos contínuos.

Usadas pela cópia de features (``audio.ingestao.preparo``), pelo VAD e pela detecção de pausas
(``audio.vocal``): assim "silêncio" significa a mesma coisa em todo o módulo.
"""

from __future__ import annotations

import math

import numpy as np

PISO_DB = -240.0
"""Nível devolvido para silêncio digital (RMS 0), em vez de -inf."""


def janela_em_amostras(taxa_hz: int, janela_ms: float) -> int:
    return max(1, round(taxa_hz * janela_ms / 1000))


def rms_por_quadro(sinal: np.ndarray, janela: int) -> np.ndarray:
    """RMS de quadros consecutivos de ``janela`` amostras (o último é completado com zeros)."""
    if sinal.size == 0:
        return np.zeros(0)
    quadros = math.ceil(sinal.size / janela)
    preenchido = np.zeros(quadros * janela, dtype=np.float64)
    preenchido[: sinal.size] = sinal
    return np.sqrt(np.mean(preenchido.reshape(quadros, janela) ** 2, axis=1))


def para_db(rms: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore"):
        return np.maximum(20.0 * np.log10(np.maximum(rms, 1e-12)), PISO_DB)


def trechos(mascara: np.ndarray) -> list[tuple[int, int]]:
    """Intervalos ``[inicio, fim)`` de valores ``True`` consecutivos."""
    if mascara.size == 0 or not mascara.any():
        return []
    bordas = np.diff(np.concatenate(([0], mascara.astype(np.int8), [0])))
    return list(zip(np.flatnonzero(bordas == 1).tolist(), np.flatnonzero(bordas == -1).tolist(),
                    strict=True))
