"""US10: normalização e remoção de silêncios só na cópia de features; o original é preservado."""

from __future__ import annotations

import hashlib

import numpy as np
import pytest
import soundfile as sf

from audio.ingestao.lote import ingerir
from audio.ingestao.preparo import (
    normalizar_pico,
    preparar_copia_features,
    remover_silencios_longos,
)

from .conftest import silencio, tom

TAXA = 16000


def _concatenar(*trechos):
    return np.concatenate(trechos)[:, 0]


def _remover(sinal, cfg):
    """(sinal sem silêncios longos, segundos removidos, trechos encurtados)."""
    sem_silencio, mantida, encurtados = remover_silencios_longos(sinal, TAXA, cfg.copia_features)
    assert mantida.size == sinal.size
    return sem_silencio, (sinal.size - sem_silencio.size) / TAXA, encurtados


def _sha(caminho):
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def test_normalizacao_leva_o_pico_ao_alvo(cfg_ingestao):
    sinal = tom(1.0, amplitude=0.3)[:, 0]

    normalizado, ganho_db = normalizar_pico(sinal, cfg_ingestao.copia_features)

    assert float(np.max(np.abs(normalizado))) == pytest.approx(cfg_ingestao.copia_features.pico_alvo,
                                                               abs=1e-3)
    assert ganho_db == pytest.approx(20 * np.log10(0.95 / 0.3), abs=0.05)


def test_ganho_e_limitado_para_nao_amplificar_so_ruido(cfg_ingestao):
    sinal = tom(1.0, amplitude=0.05)[:, 0]

    normalizado, ganho_db = normalizar_pico(sinal, cfg_ingestao.copia_features)

    assert float(np.max(np.abs(normalizado))) == pytest.approx(0.05 * 5.0, abs=1e-3)
    assert ganho_db == pytest.approx(20 * np.log10(5.0), abs=0.05)


def test_silencio_longo_no_meio_e_encurtado_para_manter_s(cfg_ingestao):
    sinal = _concatenar(tom(1.0), silencio(3.0), tom(1.0))

    sem_silencio, removido_s, trechos = _remover(sinal, cfg_ingestao)

    manter = cfg_ingestao.copia_features.silencio.manter_s
    assert sem_silencio.size / TAXA == pytest.approx(2.0 + manter, abs=0.05)
    assert removido_s == pytest.approx(3.0 - manter, abs=0.05)
    assert trechos == 1


def test_silencio_curto_e_mantido(cfg_ingestao):
    sinal = _concatenar(tom(1.0), silencio(0.5), tom(1.0))

    sem_silencio, removido_s, trechos = _remover(sinal, cfg_ingestao)

    assert sem_silencio.size == sinal.size
    assert removido_s == 0.0
    assert trechos == 0


def test_silencio_longo_nas_pontas_fica_so_com_a_metade_vizinha_a_fala(cfg_ingestao):
    sinal = _concatenar(silencio(2.0), tom(1.0), silencio(2.0))

    sem_silencio, _, trechos = _remover(sinal, cfg_ingestao)

    manter = cfg_ingestao.copia_features.silencio.manter_s
    assert sem_silencio.size / TAXA == pytest.approx(1.0 + manter, abs=0.05)
    assert trechos == 2


def test_ruido_de_fundo_fraco_conta_como_silencio(cfg_ingestao):
    fundo = silencio(3.0, ruido=0.0005)  # cerca de -60 dBFS
    sinal = _concatenar(tom(1.0), fundo, tom(1.0))

    sem_silencio, removido_s, _ = _remover(sinal, cfg_ingestao)

    assert removido_s > 2.5
    assert sem_silencio.size / TAXA < 2.5


def test_copia_de_features_informa_o_que_fez(cfg_ingestao):
    sinal = _concatenar(tom(1.0, amplitude=0.2), silencio(2.0), tom(1.0, amplitude=0.2))

    copia = preparar_copia_features(sinal, TAXA, cfg_ingestao.copia_features)

    assert copia.ganho_db > 0
    assert copia.silencio_removido_s > 1.5
    assert copia.trechos_removidos == 1
    assert copia.duracao_s == pytest.approx(copia.sinal.size / TAXA)


def test_original_e_convertido_ficam_intactos_e_so_a_copia_perde_silencio(
    tmp_path, gravar_audio, cfg_ingestao
):
    sinal = np.concatenate([tom(1.0, amplitude=0.2), silencio(3.0), tom(1.0, amplitude=0.2)])
    origem = gravar_audio("consulta.wav", sinal, TAXA, subtype="PCM_16")
    sha_antes = _sha(origem)

    resultado = ingerir(origem, tmp_path / "saida", cfg_ingestao)

    assert _sha(origem) == sha_antes
    assert resultado.metadados.original_preservado is True
    assert resultado.metadados.sha256 == sha_antes
    duracao_convertida = sf.info(str(resultado.convertido)).duration
    duracao_features = sf.info(str(resultado.features)).duration
    assert duracao_convertida == pytest.approx(5.0, abs=0.01)  # pausas preservadas
    assert duracao_features < duracao_convertida - 2.0  # silêncio longo removido só na cópia
    assert resultado.metadados.silencio_removido_s == pytest.approx(
        duracao_convertida - duracao_features, abs=0.05
    )


def test_convertido_nao_e_normalizado_mas_a_copia_e(tmp_path, gravar_audio, cfg_ingestao):
    origem = gravar_audio("baixo.wav", tom(2.0, amplitude=0.2), TAXA, subtype="PCM_16")

    resultado = ingerir(origem, tmp_path / "saida", cfg_ingestao)

    pico_convertido = float(np.max(np.abs(sf.read(str(resultado.convertido))[0])))
    pico_features = float(np.max(np.abs(sf.read(str(resultado.features))[0])))
    assert pico_convertido == pytest.approx(0.2, abs=0.01)
    assert pico_features == pytest.approx(0.95, abs=0.01)


def test_recortar_leva_um_trecho_do_original_para_a_copia(cfg_ingestao):
    """O mapa de tempo permite achar, na cópia, o trecho de um segmento da transcrição."""
    # 1 s de tom a 220 Hz, 3 s de silêncio, 1 s de tom a 440 Hz
    sinal = _concatenar(tom(1.0, freq_hz=220.0), silencio(3.0), tom(1.0, freq_hz=440.0))
    copia = preparar_copia_features(sinal, TAXA, cfg_ingestao.copia_features)

    primeiro = copia.recortar(0.0, 1.0)
    segundo = copia.recortar(4.0, 5.0)  # no original, o segundo tom começa em 4 s
    assert primeiro.size == pytest.approx(TAXA, abs=2)
    assert segundo.size == pytest.approx(TAXA, abs=2)

    def freq_dominante(x):
        espectro = np.abs(np.fft.rfft(x))
        return np.argmax(espectro) * TAXA / x.size

    assert freq_dominante(primeiro) == pytest.approx(220.0, abs=2)
    assert freq_dominante(segundo) == pytest.approx(440.0, abs=2)


def test_recortar_fora_dos_limites_nao_estoura(cfg_ingestao):
    copia = preparar_copia_features(tom(1.0)[:, 0], TAXA, cfg_ingestao.copia_features)

    assert copia.recortar(-5.0, 0.0).size == 0
    assert copia.recortar(0.5, 100.0).size > 0
    assert copia.recortar(3.0, 2.0).size == 0
