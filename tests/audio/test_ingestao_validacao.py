"""US10: áudio corrompido, vazio ou em formato não suportado vira erro claro e tipado."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest
import soundfile as sf

from app.audio_convert import ConversaoAudioError
from audio.ingestao import leitura
from audio.ingestao.erros import (
    ArquivoNaoEncontrado,
    AudioCorrompido,
    AudioCurto,
    AudioLongo,
    AudioVazio,
    FormatoNaoSuportado,
)
from audio.ingestao.leitura import ler_audio

from .conftest import silencio, tom


@pytest.fixture(autouse=True)
def _sem_ffmpeg(monkeypatch):
    """Resultado igual com ou sem ffmpeg instalado na máquina que roda os testes."""
    monkeypatch.setattr(leitura.shutil, "which", lambda _nome: None)


def test_arquivo_inexistente(tmp_path, cfg_ingestao):
    with pytest.raises(ArquivoNaoEncontrado):
        ler_audio(tmp_path / "nao_existe.wav", cfg_ingestao)


def test_arquivo_de_zero_bytes_e_audio_vazio(tmp_path, cfg_ingestao):
    vazio = tmp_path / "vazio.wav"
    vazio.write_bytes(b"")

    with pytest.raises(AudioVazio, match="0 bytes"):
        ler_audio(vazio, cfg_ingestao)


def test_wav_valido_sem_amostras_e_audio_vazio(tmp_path, gravar_audio, cfg_ingestao):
    caminho = gravar_audio("sem_amostras.wav", np.zeros((0, 1), dtype=np.float32), 16000,
                           subtype="PCM_16")

    with pytest.raises(AudioVazio):
        ler_audio(caminho, cfg_ingestao)


def test_silencio_digital_e_audio_vazio(gravar_audio, cfg_ingestao):
    caminho = gravar_audio("mudo.wav", silencio(2.0), 16000, subtype="PCM_16")

    with pytest.raises(AudioVazio, match="silêncio digital"):
        ler_audio(caminho, cfg_ingestao)


def test_bytes_aleatorios_com_extensao_wav_sao_audio_corrompido(tmp_path, cfg_ingestao):
    lixo = tmp_path / "lixo.wav"
    lixo.write_bytes(np.random.default_rng(0).bytes(4096))

    with pytest.raises(AudioCorrompido):
        ler_audio(lixo, cfg_ingestao)


def test_wav_truncado_e_audio_corrompido(tmp_path, gravar_audio, cfg_ingestao):
    inteiro = gravar_audio("inteiro.wav", tom(1.0), 16000, subtype="PCM_16")
    truncado = tmp_path / "truncado.wav"
    truncado.write_bytes(inteiro.read_bytes()[:30])  # corta no meio do cabeçalho

    with pytest.raises(AudioCorrompido):
        ler_audio(truncado, cfg_ingestao)


def test_amostras_nan_sao_audio_corrompido(gravar_audio, cfg_ingestao):
    sinal = tom(1.0)
    sinal[100, 0] = np.nan
    caminho = gravar_audio("nan.wav", sinal, 16000, subtype="FLOAT")

    with pytest.raises(AudioCorrompido, match="NaN"):
        ler_audio(caminho, cfg_ingestao)


def test_extensao_desconhecida_nao_e_suportada(tmp_path, cfg_ingestao):
    texto = tmp_path / "anotacoes.txt"
    texto.write_text("não é áudio")

    with pytest.raises(FormatoNaoSuportado, match=r"\.txt"):
        ler_audio(texto, cfg_ingestao)


def test_m4a_sem_ffmpeg_pede_a_instalacao(tmp_path, cfg_ingestao):
    m4a = tmp_path / "consulta.m4a"
    m4a.write_bytes(b"\x00\x00\x00\x20ftypM4A " + bytes(64))

    with pytest.raises(FormatoNaoSuportado, match="ffmpeg"):
        ler_audio(m4a, cfg_ingestao)


def test_audio_curto_demais(gravar_audio, cfg_ingestao):
    caminho = gravar_audio("curto.wav", tom(0.2), 16000, subtype="PCM_16")

    with pytest.raises(AudioCurto, match="mínimo"):
        ler_audio(caminho, cfg_ingestao)


def test_audio_longo_demais_e_recusado_antes_de_decodificar(gravar_audio, cfg_ingestao):
    caminho = gravar_audio("longo.wav", tom(3.0), 16000, subtype="PCM_16")
    cfg = dataclasses.replace(
        cfg_ingestao, validacao=dataclasses.replace(cfg_ingestao.validacao, duracao_maxima_s=2.0)
    )

    with pytest.raises(AudioLongo, match="máximo"):
        ler_audio(caminho, cfg)


def test_formato_nao_nativo_usa_ffmpeg_quando_disponivel(tmp_path, monkeypatch, cfg_ingestao):
    m4a = tmp_path / "consulta.m4a"
    m4a.write_bytes(bytes(256))
    chamadas = {}

    def falso_converter(origem, destino, *, timeout_s):
        chamadas.update(origem=origem, timeout_s=timeout_s)
        sf.write(str(destino), tom(1.0)[:, 0], 16000, subtype="PCM_16")
        return destino

    monkeypatch.setattr(leitura.shutil, "which", lambda _nome: "/usr/bin/ffmpeg")
    monkeypatch.setattr(leitura, "converter_para_wav", falso_converter)

    audio = ler_audio(m4a, cfg_ingestao)

    assert audio.decodificador == "ffmpeg"
    assert audio.formato_origem == "m4a"
    assert audio.taxa_hz == 16000
    assert audio.taxa_origem_hz is None  # o ffmpeg converte direto; a taxa original se perde
    assert chamadas["timeout_s"] == cfg_ingestao.validacao.ffmpeg_timeout_s


def test_falha_do_ffmpeg_vira_audio_corrompido(tmp_path, monkeypatch, cfg_ingestao):
    m4a = tmp_path / "ruim.m4a"
    m4a.write_bytes(bytes(256))

    def falha(origem, destino, *, timeout_s):
        raise ConversaoAudioError("ffmpeg falhou em 'ruim.m4a': Invalid data")

    monkeypatch.setattr(leitura.shutil, "which", lambda _nome: "/usr/bin/ffmpeg")
    monkeypatch.setattr(leitura, "converter_para_wav", falha)

    with pytest.raises(AudioCorrompido, match="Invalid data"):
        ler_audio(m4a, cfg_ingestao)


def test_erros_carregam_codigo_estavel():
    assert AudioVazio.codigo == "audio_vazio"
    assert AudioCorrompido.codigo == "audio_corrompido"
    assert FormatoNaoSuportado.codigo == "formato_nao_suportado"
    assert AudioCurto.codigo == "audio_curto"


def test_sem_caminho_troca_o_caminho_absoluto_pelo_nome_do_arquivo():
    from pathlib import Path

    from audio.ingestao.leitura import _sem_caminho

    arquivo = Path("/tmp/pytest-0/entrada/corrompido.wav")
    mensagem = f"Error opening '{arquivo}': Format not recognised. Pasta: {arquivo.parent}"

    limpa = _sem_caminho(mensagem, arquivo)

    assert "corrompido.wav" in limpa
    assert str(arquivo.parent) not in limpa
