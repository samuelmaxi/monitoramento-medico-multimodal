import subprocess
from types import SimpleNamespace

import pytest

from app import audio_convert
from app.audio_convert import ConversaoAudioError, converter_para_wav


def test_sem_ffmpeg_no_path_levanta_erro_claro(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_convert.shutil, "which", lambda _: None)

    with pytest.raises(ConversaoAudioError, match="ffmpeg não encontrado"):
        converter_para_wav(tmp_path / "a.m4a", tmp_path / "a.wav")


def test_comando_do_ffmpeg_usa_16khz_mono_pcm(tmp_path, monkeypatch):
    capturado = {}

    def executar(comando, **kwargs):
        capturado["comando"] = comando
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(audio_convert.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(audio_convert.subprocess, "run", executar)

    converter_para_wav(tmp_path / "a.m4a", tmp_path / "saida" / "a.wav")

    comando = capturado["comando"]
    assert comando[comando.index("-ac") + 1] == "1"
    assert comando[comando.index("-ar") + 1] == "16000"
    assert comando[comando.index("-c:a") + 1] == "pcm_s16le"


def test_falha_do_ffmpeg_levanta_erro_com_mensagem(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_convert.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        audio_convert.subprocess,
        "run",
        lambda comando, **kw: SimpleNamespace(returncode=1, stderr="Invalid data"),
    )

    with pytest.raises(ConversaoAudioError, match="Invalid data"):
        converter_para_wav(tmp_path / "ruim.m4a", tmp_path / "ruim.wav")
