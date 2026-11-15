"""Conversão para WAV PCM 16 kHz, 16 bits, mono, e validação do arquivo gravado."""

from __future__ import annotations

from dataclasses import dataclass
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .config import ConfigIngestao
from .erros import AudioCorrompido
from .leitura import AudioLido

SUBTIPO_SAIDA = "PCM_16"
CANAIS_SAIDA = 1


@dataclass(frozen=True)
class ValidacaoWav:
    """Resultado de :func:`validar_wav`: cada falha é uma frase legível."""

    formato: str
    subtipo: str
    taxa_hz: int
    canais: int
    amostras: int
    duracao_s: float
    falhas: tuple[str, ...]

    @property
    def valido(self) -> bool:
        return not self.falhas


def para_mono_na_taxa(sinal: np.ndarray, taxa_origem_hz: int, taxa_destino_hz: int) -> np.ndarray:
    """Média dos canais e reamostragem polifásica; devolve float32 1-D em [-1, 1]."""
    mono = sinal.mean(axis=1) if sinal.ndim == 2 else sinal
    if taxa_origem_hz != taxa_destino_hz:
        divisor = gcd(taxa_destino_hz, taxa_origem_hz)
        mono = resample_poly(mono, taxa_destino_hz // divisor, taxa_origem_hz // divisor)
    pico = float(np.max(np.abs(mono))) if mono.size else 0.0
    if pico > 1.0:  # a reamostragem ou o downmix pode estourar; reduz em vez de cortar
        mono = mono / pico
    return np.clip(mono, -1.0, 1.0).astype(np.float32)


def validar_wav(caminho: str | Path, cfg: ConfigIngestao) -> ValidacaoWav:
    """Confere se ``caminho`` é WAV PCM 16 bits, mono, na taxa configurada, com duração mínima."""
    caminho = Path(caminho)
    try:
        info = sf.info(str(caminho))
    except (sf.LibsndfileError, RuntimeError, OSError) as erro:
        raise AudioCorrompido(f"'{caminho.name}' não é um áudio legível: {erro}") from erro
    falhas: list[str] = []
    if info.format != "WAV":
        falhas.append(f"formato {info.format}, esperado WAV")
    if info.subtype != SUBTIPO_SAIDA:
        falhas.append(f"codificação {info.subtype}, esperado {SUBTIPO_SAIDA}")
    if info.samplerate != cfg.taxa_amostragem_hz:
        falhas.append(f"{info.samplerate} Hz, esperado {cfg.taxa_amostragem_hz} Hz")
    if info.channels != CANAIS_SAIDA:
        falhas.append(f"{info.channels} canais, esperado mono")
    if info.frames == 0:
        falhas.append("sem amostras")
    elif info.frames / info.samplerate < cfg.validacao.duracao_minima_s:
        falhas.append(f"duração abaixo de {cfg.validacao.duracao_minima_s:g} s")
    return ValidacaoWav(
        formato=info.format,
        subtipo=info.subtype,
        taxa_hz=info.samplerate,
        canais=info.channels,
        amostras=info.frames,
        duracao_s=info.frames / info.samplerate if info.samplerate else 0.0,
        falhas=tuple(falhas),
    )


def converter(audio: AudioLido, destino: str | Path, cfg: ConfigIngestao) -> np.ndarray:
    """Grava ``destino`` (WAV PCM 16 kHz mono), relê o arquivo e confere o formato.

    Devolve o sinal convertido (float32, 1-D), já em memória para as próximas etapas.
    """
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    sinal = para_mono_na_taxa(audio.sinal, audio.taxa_hz, cfg.taxa_amostragem_hz)
    sf.write(str(destino), sinal, cfg.taxa_amostragem_hz, subtype=SUBTIPO_SAIDA, format="WAV")
    validacao = validar_wav(destino, cfg)
    if not validacao.valido:
        destino.unlink(missing_ok=True)
        raise AudioCorrompido(
            f"conversão de '{destino.name}' gerou arquivo fora do formato: "
            + "; ".join(validacao.falhas)
        )
    return sinal
