"""Biomarcadores vocais (US12/US14) dentro da etapa de áudio do pipeline (US20)."""

import json

import numpy as np
import pytest
import soundfile as sf

from pipeline import ConfigPipeline, OrquestradorPipeline


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.setenv("PSEUDONYM_KEY", "chave-de-teste-com-mais-de-32-bytes-000000")
    for var in ("AZURE_SPEECH_KEY", "AZURE_SPEECH_ENDPOINT", "AZURE_LANGUAGE_KEY", "AZURE_LANGUAGE_ENDPOINT"):
        monkeypatch.delenv(var, raising=False)


def _executar(tmp_path, **config):
    cfg = ConfigPipeline.from_env(
        fator_velocidade=0.0, saida_dir=tmp_path / "saida", sinais_fonte="sintetico", **config
    )
    resumo = OrquestradorPipeline(cfg).executar()
    relatorio = json.loads(resumo.caminho_relatorio.read_text(encoding="utf-8"))
    return resumo, next(e for e in relatorio["etapas"] if e["etapa"] == "audio")


def test_sem_audio_usa_achado_vocal_de_demonstracao(tmp_path):
    _, etapa = _executar(tmp_path)
    assert "sem áudio" in etapa["detalhes"]["biomarcadores_vocais"]


def test_com_audio_roda_audio_vocal_e_publica_no_barramento(tmp_path):
    t = np.arange(16000 * 4) / 16000
    wav = tmp_path / "paciente.wav"
    sf.write(str(wav), (0.4 * np.sin(2 * np.pi * 180 * t)).astype(np.float32), 16000)

    resumo, etapa = _executar(tmp_path, audio_path=wav)

    assert etapa["status"] == "sucesso"
    assert etapa["detalhes"]["biomarcadores_vocais"].startswith("audio.vocal")
    assert etapa["detalhes"]["eventos_vocais"] >= 1
    assert (tmp_path / "saida" / "audio" / "paciente" / "relatorio.json").is_file()
    linhas = resumo.caminho_eventos.read_text(encoding="utf-8").splitlines()
    assert any(json.loads(linha)["modality"] == "audio" for linha in linhas)


def test_audio_invalido_cai_no_achado_de_demonstracao_e_registra_o_motivo(tmp_path):
    wav = tmp_path / "quebrado.wav"
    wav.write_bytes(b"isto nao e um wav")
    _, etapa = _executar(tmp_path, audio_path=wav)
    assert etapa["status"] == "sucesso"
    assert "falhou" in etapa["detalhes"]["biomarcadores_vocais"]
