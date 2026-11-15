"""Cópia do áudio usada na extração de features: volume normalizado e silêncios longos removidos.

Só a cópia é alterada; o arquivo original e o ``convertido.wav`` ficam como estão. Pausas e taxa
de fala (US12) são medidas no ``convertido.wav``: esta cópia perde justamente a duração das
pausas longas, o que serve a jitter, shimmer, HNR, F0 e intensidade, mas não a elas.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from audio.sinal import janela_em_amostras, para_db, rms_por_quadro, trechos

from .config import CopiaFeaturesCfg


@dataclass(frozen=True, eq=False)
class CopiaFeatures:
    """Cópia normalizada e sem silêncios longos, mais o mapa de volta ao tempo do original.

    ``indice[i]`` é a posição na cópia da amostra ``i`` do áudio original (e ``indice[-1]`` o
    tamanho da cópia): com ele, um trecho da fala no tempo do ``convertido.wav`` (por exemplo, um
    segmento da transcrição) vira o trecho equivalente da cópia.
    """

    sinal: np.ndarray
    taxa_hz: int
    ganho_db: float
    silencio_removido_s: float
    trechos_removidos: int
    duracao_s: float
    indice: np.ndarray

    def recortar(self, inicio_s: float, fim_s: float) -> np.ndarray:
        """Trecho da cópia que corresponde a ``[inicio_s, fim_s)`` do áudio original."""
        n = self.indice.size - 1
        a = min(max(round(inicio_s * self.taxa_hz), 0), n)
        b = min(max(round(fim_s * self.taxa_hz), a), n)
        return self.sinal[self.indice[a] : self.indice[b]]


def normalizar_pico(sinal: np.ndarray, cfg: CopiaFeaturesCfg) -> tuple[np.ndarray, float]:
    """Leva o pico a ``pico_alvo``, com ganho limitado a ``ganho_maximo``: (sinal, ganho_db)."""
    pico = float(np.max(np.abs(sinal))) if sinal.size else 0.0
    if pico <= 0.0:
        return sinal.astype(np.float32), 0.0
    ganho = min(cfg.pico_alvo / pico, cfg.ganho_maximo)
    return (sinal * ganho).astype(np.float32), 20.0 * math.log10(ganho)


def remover_silencios_longos(
    sinal: np.ndarray, taxa_hz: int, cfg: CopiaFeaturesCfg
) -> tuple[np.ndarray, np.ndarray, int]:
    """Encurta cada silêncio de pelo menos ``duracao_longa_s`` para ``manter_s``.

    Silêncio = quadro com RMS ``limiar_relativo_db`` abaixo do nível de fala da gravação
    (percentil 95 dos RMS). Nas pontas do arquivo fica só a metade vizinha à fala; no meio,
    metade de cada lado. Devolve ``(sinal, mascara_mantida, trechos_encurtados)``; a máscara tem o
    tamanho do sinal de entrada e diz que amostras sobreviveram.
    """
    s = cfg.silencio
    janela = janela_em_amostras(taxa_hz, s.janela_ms)
    rms_db = para_db(rms_por_quadro(sinal, janela))
    nivel_fala_db = float(np.percentile(rms_db, 95))
    silencioso = rms_db < nivel_fala_db + s.limiar_relativo_db

    longa = round(s.duracao_longa_s * taxa_hz)
    meio = round(s.manter_s * taxa_hz / 2)
    manter = np.ones(sinal.size, dtype=bool)
    encurtados = 0
    for inicio_q, fim_q in trechos(silencioso):
        inicio = inicio_q * janela
        fim = min(fim_q * janela, sinal.size)
        if fim - inicio < longa:
            continue
        a = inicio if inicio == 0 else inicio + meio
        b = fim if fim == sinal.size else fim - meio
        if b > a:
            manter[a:b] = False
            encurtados += 1
    return sinal[manter], manter, encurtados


def preparar_copia_features(
    sinal: np.ndarray, taxa_hz: int, cfg: CopiaFeaturesCfg
) -> CopiaFeatures:
    normalizado, ganho_db = normalizar_pico(sinal, cfg)
    sem_silencio, mantida, encurtados = remover_silencios_longos(normalizado, taxa_hz, cfg)
    return CopiaFeatures(
        sinal=sem_silencio,
        taxa_hz=taxa_hz,
        ganho_db=round(ganho_db, 2),
        silencio_removido_s=round((sinal.size - sem_silencio.size) / taxa_hz, 3),
        trechos_removidos=encurtados,
        duracao_s=sem_silencio.size / taxa_hz,
        indice=np.concatenate(([0], np.cumsum(mantida))).astype(np.int64),
    )
