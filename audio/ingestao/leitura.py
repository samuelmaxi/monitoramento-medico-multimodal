"""Leitura e validação de entrada: decodifica o áudio ou levanta um :class:`ErroDeAudio` claro.

O libsndfile (via ``soundfile``) decodifica WAV, FLAC e OGG/Opus (o formato dos áudios de voz
do WhatsApp) sem ffmpeg. MP3, M4A, AAC, WMA, AMR e WebM caem no ffmpeg
(``app.audio_convert``); sem ele, o erro diz o que instalar.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from app.audio_convert import ConversaoAudioError, converter_para_wav

from .config import ConfigIngestao
from .erros import (
    ArquivoNaoEncontrado,
    AudioCorrompido,
    AudioCurto,
    AudioLongo,
    AudioVazio,
    FormatoNaoSuportado,
)

log = logging.getLogger("audio.ingestao")

_ERROS_DO_LIBSNDFILE = (sf.LibsndfileError, RuntimeError, OSError)


@dataclass(frozen=True)
class AudioLido:
    """Áudio decodificado. ``sinal`` é float32 em [-1, 1], com forma ``(amostras, canais)``.

    Pelo ffmpeg o áudio já chega em 16 kHz mono, e ``taxa_origem_hz``/``canais_origem``/
    ``duracao_origem_s`` ficam ``None``: o ffmpeg converte direto e a taxa original se perde.
    """

    sinal: np.ndarray
    taxa_hz: int
    formato_origem: str
    codec_origem: str | None
    decodificador: str
    taxa_origem_hz: int | None
    canais_origem: int | None
    duracao_origem_s: float | None

    @property
    def canais(self) -> int:
        return int(self.sinal.shape[1])

    @property
    def duracao_s(self) -> float:
        return self.sinal.shape[0] / self.taxa_hz


def _sem_caminho(texto: str, caminho: Path) -> str:
    """Tira de ``texto`` o caminho do arquivo (o libsndfile e o ffmpeg o repetem na mensagem).

    O erro vai para o log e para a API, que só devem mostrar o nome do arquivo.
    """
    return texto.replace(str(caminho), caminho.name).replace(str(caminho.parent), "").strip()


class _FalhaDeDecodificacao(Exception):
    pass


def extensao_de(caminho: Path) -> str:
    return caminho.suffix.lower().lstrip(".")


def _validar_sinal(sinal: np.ndarray, taxa_hz: int, cfg: ConfigIngestao, nome: str) -> None:
    if sinal.size == 0 or sinal.shape[0] == 0:
        raise AudioVazio(f"'{nome}' não tem amostras de áudio")
    if taxa_hz <= 0:
        raise AudioCorrompido(f"'{nome}' tem taxa de amostragem inválida ({taxa_hz})")
    if not np.isfinite(sinal).all():
        raise AudioCorrompido(f"'{nome}' tem amostras NaN/Inf")
    if float(np.max(np.abs(sinal))) < cfg.validacao.pico_minimo:
        raise AudioVazio(
            f"'{nome}' é silêncio digital (pico abaixo de {cfg.validacao.pico_minimo})"
        )
    duracao = sinal.shape[0] / taxa_hz
    if duracao < cfg.validacao.duracao_minima_s:
        raise AudioCurto(
            f"'{nome}' tem {duracao:.2f} s; o mínimo é {cfg.validacao.duracao_minima_s:g} s"
        )
    if duracao > cfg.validacao.duracao_maxima_s:
        raise AudioLongo(
            f"'{nome}' tem {duracao:.0f} s; o máximo é {cfg.validacao.duracao_maxima_s:g} s"
        )


def _ler_com_libsndfile(caminho: Path, extensao: str, cfg: ConfigIngestao) -> AudioLido:
    try:
        info = sf.info(str(caminho))
    except _ERROS_DO_LIBSNDFILE as erro:
        raise _FalhaDeDecodificacao(str(erro)) from erro
    # Confere a duração antes de decodificar, para não carregar horas de áudio na memória.
    if info.frames == 0:
        raise AudioVazio(f"'{caminho.name}' não tem amostras de áudio")
    if info.samplerate > 0 and info.frames / info.samplerate > cfg.validacao.duracao_maxima_s:
        raise AudioLongo(
            f"'{caminho.name}' tem {info.frames / info.samplerate:.0f} s; "
            f"o máximo é {cfg.validacao.duracao_maxima_s:g} s"
        )
    try:
        sinal, taxa = sf.read(str(caminho), dtype="float32", always_2d=True)
    except _ERROS_DO_LIBSNDFILE as erro:
        raise _FalhaDeDecodificacao(str(erro)) from erro
    _validar_sinal(sinal, int(taxa), cfg, caminho.name)
    return AudioLido(
        sinal=sinal,
        taxa_hz=int(taxa),
        formato_origem=extensao,
        codec_origem=info.subtype,
        decodificador="libsndfile",
        taxa_origem_hz=int(taxa),
        canais_origem=int(sinal.shape[1]),
        duracao_origem_s=sinal.shape[0] / int(taxa),
    )


def _ler_com_ffmpeg(caminho: Path, extensao: str, cfg: ConfigIngestao) -> AudioLido:
    with tempfile.TemporaryDirectory(prefix="audio-ingestao-") as pasta:
        decodificado = Path(pasta) / "decodificado.wav"
        try:
            converter_para_wav(caminho, decodificado, timeout_s=cfg.validacao.ffmpeg_timeout_s)
            sinal, taxa = sf.read(str(decodificado), dtype="float32", always_2d=True)
        except ConversaoAudioError as erro:
            raise AudioCorrompido(
                f"'{caminho.name}' não pôde ser decodificado: {_sem_caminho(str(erro), caminho)}"
            ) from erro
        except _ERROS_DO_LIBSNDFILE as erro:
            raise AudioCorrompido(
                f"'{caminho.name}': saída do ffmpeg ilegível ({erro})"
            ) from erro
    _validar_sinal(sinal, int(taxa), cfg, caminho.name)
    return AudioLido(
        sinal=sinal,
        taxa_hz=int(taxa),
        formato_origem=extensao,
        codec_origem=None,
        decodificador="ffmpeg",
        taxa_origem_hz=None,
        canais_origem=None,
        duracao_origem_s=None,
    )


def ler_audio(caminho: str | Path, cfg: ConfigIngestao) -> AudioLido:
    """Decodifica ``caminho`` ou levanta :class:`ErroDeAudio`. Nunca altera o arquivo."""
    caminho = Path(caminho)
    if not caminho.is_file():
        raise ArquivoNaoEncontrado(f"'{caminho.name}' não existe ou não é um arquivo")
    extensao = extensao_de(caminho)
    if extensao not in cfg.validacao.extensoes:
        raise FormatoNaoSuportado(
            f"extensão '.{extensao}' não suportada em '{caminho.name}'. "
            f"Aceitas: {sorted(cfg.validacao.extensoes)}"
        )
    if caminho.stat().st_size == 0:
        raise AudioVazio(f"'{caminho.name}' tem 0 bytes")

    try:
        return _ler_com_libsndfile(caminho, extensao, cfg)
    except _FalhaDeDecodificacao as falha:
        motivo = _sem_caminho(str(falha), caminho)
    log.debug("libsndfile não leu '%s' (%s); tentando ffmpeg", caminho.name, motivo)

    if shutil.which("ffmpeg") is None:
        if extensao in cfg.validacao.decodificador_nativo:
            raise AudioCorrompido(f"'{caminho.name}' não pôde ser decodificado: {motivo}")
        raise FormatoNaoSuportado(
            f"'.{extensao}' precisa do ffmpeg no PATH para ser decodificado "
            "(ex.: winget install Gyan.FFmpeg)"
        )
    return _ler_com_ffmpeg(caminho, extensao, cfg)
