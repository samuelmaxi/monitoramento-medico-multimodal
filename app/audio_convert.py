"""Conversão de áudio para WAV 16 kHz mono com ffmpeg (US11).

A transcrição rápida não aceita `.m4a`/AAC em contêiner MP4, então os áudios de
amostra são convertidos antes. O ffmpeg precisa estar no PATH.
"""

import shutil
import subprocess
from pathlib import Path


class ConversaoAudioError(Exception):
    """Falha ao converter o áudio, inclusive quando o ffmpeg não está instalado."""


TIMEOUT_PADRAO_S = 300.0


def converter_para_wav(origem: Path, destino: Path, *, timeout_s: float = TIMEOUT_PADRAO_S) -> Path:
    """Gera `destino` (WAV PCM 16 kHz mono). O arquivo de origem não é alterado.

    `timeout_s` evita que um arquivo malformado trave o lote: passado o prazo, o ffmpeg é
    encerrado e a falha vira `ConversaoAudioError`.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ConversaoAudioError(
            "ffmpeg não encontrado no PATH. Instale-o (ex.: winget install Gyan.FFmpeg)."
        )

    destino.parent.mkdir(parents=True, exist_ok=True)
    comando = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", str(origem),
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(destino),
    ]
    try:
        resultado = subprocess.run(
            comando, capture_output=True, text=True, check=False, timeout=timeout_s
        )
    except subprocess.TimeoutExpired as exc:
        raise ConversaoAudioError(
            f"ffmpeg excedeu {timeout_s:g} s em '{origem.name}' e foi encerrado."
        ) from exc
    if resultado.returncode != 0:
        raise ConversaoAudioError(f"ffmpeg falhou em '{origem.name}': {resultado.stderr.strip()}")
    return destino
