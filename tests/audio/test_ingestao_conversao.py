"""US10: conversão para WAV PCM 16 kHz / 16 bits / mono e validação do formato gerado."""

from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from audio.ingestao.conversao import converter, para_mono_na_taxa, validar_wav
from audio.ingestao.leitura import ler_audio
from audio.ingestao.lote import ingerir

from .conftest import tom


def _info(caminho):
    return sf.info(str(caminho))


@pytest.mark.parametrize(
    ("taxa", "canais"), [(44100, 2), (48000, 1), (8000, 1), (22050, 2), (16000, 2)]
)
def test_converte_qualquer_taxa_e_canais_para_16khz_mono_pcm16(
    tmp_path, gravar_audio, cfg_ingestao, taxa, canais
):
    origem = gravar_audio("entrada.wav", tom(2.0, taxa, canais=canais), taxa, subtype="FLOAT")

    resultado = ingerir(origem, tmp_path / "saida", cfg_ingestao)

    info = _info(resultado.convertido)
    assert (info.format, info.subtype, info.samplerate, info.channels) == ("WAV", "PCM_16", 16000, 1)
    assert info.frames / info.samplerate == pytest.approx(2.0, abs=0.01)


def test_ogg_vorbis_e_lido_sem_ffmpeg(tmp_path, gravar_audio, cfg_ingestao):
    origem = gravar_audio("voz.ogg", tom(1.5, 44100), 44100, format="OGG", subtype="VORBIS")

    resultado = ingerir(origem, tmp_path / "saida", cfg_ingestao)

    assert resultado.metadados.decodificador == "libsndfile"
    assert resultado.metadados.formato_origem == "ogg"
    assert resultado.metadados.codec_origem == "VORBIS"
    assert resultado.metadados.taxa_amostragem_hz == 44100


def test_ogg_opus_dos_audios_de_whatsapp_e_lido_sem_ffmpeg(tmp_path, gravar_audio, cfg_ingestao):
    try:
        origem = gravar_audio("ptt.ogg", tom(1.5, 16000), 16000, format="OGG", subtype="OPUS")
    except (sf.LibsndfileError, RuntimeError):  # pragma: no cover - libsndfile sem escrita Opus
        pytest.skip("libsndfile desta máquina não grava Opus")

    resultado = ingerir(origem, tmp_path / "saida", cfg_ingestao)

    assert resultado.metadados.codec_origem == "OPUS"
    assert _info(resultado.convertido).samplerate == 16000


def test_pcm_acima_de_1_nao_estoura_na_conversao():
    sinal = 2.0 * tom(1.0, 16000, amplitude=1.0)  # float fora de [-1, 1]

    mono = para_mono_na_taxa(sinal, 16000, 16000)

    assert float(np.max(np.abs(mono))) <= 1.0
    assert mono.dtype == np.float32
    assert mono.ndim == 1


def test_validar_wav_aceita_o_formato_alvo(tmp_path, gravar_audio, cfg_ingestao):
    caminho = gravar_audio("ok.wav", tom(1.0), 16000, subtype="PCM_16")

    validacao = validar_wav(caminho, cfg_ingestao)

    assert validacao.valido
    assert validacao.falhas == ()
    assert validacao.duracao_s == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("sinal", "taxa", "subtype", "trecho_da_falha"),
    [
        (tom(1.0, 16000), 16000, "PCM_24", "PCM_24"),
        (tom(1.0, 16000), 16000, "FLOAT", "FLOAT"),
        (tom(1.0, 8000), 8000, "PCM_16", "8000 Hz"),
        (tom(1.0, 16000, canais=2), 16000, "PCM_16", "2 canais"),
        (tom(0.2, 16000), 16000, "PCM_16", "duração abaixo"),
    ],
)
def test_validar_wav_aponta_cada_desvio_do_formato(
    gravar_audio, cfg_ingestao, sinal, taxa, subtype, trecho_da_falha
):
    caminho = gravar_audio("fora.wav", sinal, taxa, subtype=subtype)

    validacao = validar_wav(caminho, cfg_ingestao)

    assert not validacao.valido
    assert any(trecho_da_falha in falha for falha in validacao.falhas)


def test_validar_wav_rejeita_formato_que_nao_e_wav(gravar_audio, cfg_ingestao):
    caminho = gravar_audio("fora.flac", tom(1.0), 16000, format="FLAC", subtype="PCM_16")

    assert any("FLAC" in f for f in validar_wav(caminho, cfg_ingestao).falhas)


def test_converter_grava_o_arquivo_relido_no_formato_alvo(tmp_path, gravar_audio, cfg_ingestao):
    origem = gravar_audio("a.wav", tom(1.0, 32000, canais=2), 32000, subtype="FLOAT")
    audio = ler_audio(origem, cfg_ingestao)

    sinal = converter(audio, tmp_path / "novo" / "convertido.wav", cfg_ingestao)

    assert validar_wav(tmp_path / "novo" / "convertido.wav", cfg_ingestao).valido
    assert sinal.shape == (16000,)
