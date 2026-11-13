"""US12: biomarcadores vocais medidos com Praat em voz sintética de parâmetros conhecidos."""

from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pytest

from audio.ingestao.preparo import preparar_copia_features
from audio.vocal.biomarcadores import (
    PRAAT_VERSAO,
    MedidaSegmento,
    agregar,
    medir_segmentos,
)
from audio.vocal.erros import SemFalaDoPaciente
from audio.vocal.injecao import (
    Frase,
    ParametrosVoz,
    fala_normal,
    montar_fala,
    sintetizar_voz,
    transcricao_sintetica,
)
from audio.vocal.segmentos import segmentos_da_transcricao

TAXA = 16000


def medir(sinal, falas, cfg_ingestao, cfg_vocal, **kw):
    """Roda segmentos → cópia de features → medidas → agregação, como a análise faz."""
    copia = preparar_copia_features(sinal, TAXA, cfg_ingestao.copia_features)
    segmentos = segmentos_da_transcricao(transcricao_sintetica(falas, **kw)).segmentos
    return agregar(medir_segmentos(sinal, copia, segmentos, cfg_vocal))


def f0_esperado(duracao_s, params=None):
    """Média de F0 que o gerador produz: o contorno de melodia reinicia a cada frase."""
    params = params or ParametrosVoz()
    t = np.linspace(0, duracao_s, 20000, endpoint=False)
    return float((params.f0_hz * 2 ** (params.variacao_f0_st * np.sin(2 * np.pi * 0.7 * t) / 12)).mean())


def fala_com(params, *, frases=4, palavras=8, duracao=2.4, pausa=0.5, semente=0):
    return montar_fala(
        [Frase(duracao, palavras, pausa) for _ in range(frases)], params, semente=semente)


# --- voz normal ----------------------------------------------------------------------------- #


def test_voz_normal_fica_dentro_dos_limiares_de_normalidade(cfg_ingestao, cfg_vocal):
    sinal, falas = fala_normal()

    bio = medir(sinal, falas, cfg_ingestao, cfg_vocal)

    lim = cfg_vocal.limiares
    assert bio.jitter_local_pct < lim.jitter_local_pct_max
    assert bio.shimmer_local_pct < lim.shimmer_local_pct_max
    assert bio.hnr_db > lim.hnr_db_min
    assert bio.f0_media_hz == pytest.approx(f0_esperado(2.4), abs=3.0)
    assert bio.f0_desvio_st > lim.f0_desvio_st_min  # melodia natural
    assert bio.segmentos == 4


def test_pausas_e_taxa_de_fala_de_uma_fala_normal(cfg_ingestao, cfg_vocal):
    sinal, falas = fala_normal()  # 4 frases de 2,4 s com 8 palavras e 0,5 s de pausa entre elas

    bio = medir(sinal, falas, cfg_ingestao, cfg_vocal)

    assert bio.palavras == 32
    assert bio.duracao_fala_s == pytest.approx(4 * 2.4 + 3 * 0.5, abs=0.15)
    assert bio.pausa_total_s == pytest.approx(3 * 0.5, abs=0.15)
    assert bio.n_pausas == 3
    assert bio.pausa_media_s == pytest.approx(0.5, abs=0.1)
    assert bio.proporcao_pausas == pytest.approx(1.5 / 11.1, abs=0.03)
    assert bio.taxa_fala_pps == pytest.approx(32 / 11.1, abs=0.15)
    assert bio.taxa_articulacao_pps == pytest.approx(32 / 9.6, abs=0.15)
    assert bio.taxa_articulacao_pps > bio.taxa_fala_pps  # a articulação exclui as pausas


# --- alterações injetadas ------------------------------------------------------------------- #


def test_jitter_alto_ultrapassa_o_limiar(cfg_ingestao, cfg_vocal):
    sinal, falas = fala_com(ParametrosVoz(jitter_pct=2.0))

    bio = medir(sinal, falas, cfg_ingestao, cfg_vocal)

    assert bio.jitter_local_pct > cfg_vocal.limiares.jitter_local_pct_max


def test_shimmer_alto_ultrapassa_o_limiar(cfg_ingestao, cfg_vocal):
    sinal, falas = fala_com(ParametrosVoz(shimmer_pct=8.0))

    bio = medir(sinal, falas, cfg_ingestao, cfg_vocal)

    assert bio.shimmer_local_pct > cfg_vocal.limiares.shimmer_local_pct_max


def test_ruido_derruba_o_hnr_abaixo_do_limiar(cfg_ingestao, cfg_vocal):
    sinal, falas = fala_com(ParametrosVoz(snr_db=10.0))

    bio = medir(sinal, falas, cfg_ingestao, cfg_vocal)

    assert bio.hnr_db < cfg_vocal.limiares.hnr_db_min


def test_melodia_achatada_reduz_a_variacao_de_f0(cfg_ingestao, cfg_vocal):
    sinal, falas = fala_com(ParametrosVoz(variacao_f0_st=0.8))

    bio = medir(sinal, falas, cfg_ingestao, cfg_vocal)

    assert bio.f0_desvio_st < cfg_vocal.limiares.f0_desvio_st_min


def test_pausas_longas_aumentam_a_proporcao_de_pausas(cfg_ingestao, cfg_vocal):
    normal = medir(*fala_com(ParametrosVoz()), cfg_ingestao, cfg_vocal)
    sinal, falas = fala_com(ParametrosVoz(), duracao=1.5, palavras=3, pausa=1.8)

    com_pausas = medir(sinal, falas, cfg_ingestao, cfg_vocal)

    assert com_pausas.proporcao_pausas > cfg_vocal.limiares.proporcao_pausas_max
    assert com_pausas.proporcao_pausas > normal.proporcao_pausas * 2
    assert com_pausas.taxa_fala_pps < cfg_vocal.limiares.taxa_fala_pps_min


# --- pausa dentro do segmento e o papel da cópia de features -------------------------------- #


def test_pausa_dentro_de_um_unico_segmento_e_detectada(cfg_ingestao, cfg_vocal):
    voz = sintetizar_voz(1.0, ParametrosVoz())
    fundo = np.zeros(round(0.8 * TAXA), dtype=np.float32)
    sinal = np.concatenate([np.zeros(TAXA // 4, np.float32), voz, fundo, voz])
    falas = [{"texto": "a b c d", "offset_ms": 250, "duracao_ms": 2800, "palavras": [
        {"texto": "x", "offset_ms": 250, "duracao_ms": 100}] * 4}]

    bio = medir(sinal, falas, cfg_ingestao, cfg_vocal)

    assert bio.segmentos == 1
    assert bio.n_pausas == 1
    assert bio.pausa_total_s == pytest.approx(0.8, abs=0.1)


def test_pausas_sao_medidas_no_original_mesmo_com_silencio_cortado_da_copia(
    cfg_ingestao, cfg_vocal
):
    """A cópia de features perde os silêncios longos; a proporção de pausas não pode perdê-los."""
    sinal, falas = montar_fala(
        [Frase(2.0, 6, 3.0), Frase(2.0, 6, 0.0)], ParametrosVoz())  # 3 s de pausa entre as frases
    copia = preparar_copia_features(sinal, TAXA, cfg_ingestao.copia_features)
    assert copia.silencio_removido_s > 2.0  # o corte aconteceu na cópia

    bio = medir(sinal, falas, cfg_ingestao, cfg_vocal)

    assert bio.pausa_total_s == pytest.approx(3.0, abs=0.2)
    assert bio.proporcao_pausas == pytest.approx(3.0 / 7.0, abs=0.04)
    assert bio.jitter_local_pct is not None  # e as medidas de voz continuam alinhadas à cópia
    assert bio.f0_media_hz == pytest.approx(f0_esperado(2.0), abs=4.0)


def test_voz_do_medico_nao_entra_nas_medidas_do_paciente(cfg_ingestao, cfg_vocal):
    paciente = sintetizar_voz(2.5, ParametrosVoz(f0_hz=140.0, variacao_f0_st=0.0), semente=1)
    medico = sintetizar_voz(2.5, ParametrosVoz(f0_hz=220.0, variacao_f0_st=0.0), semente=2)
    pausa = np.zeros(TAXA // 2, np.float32)
    sinal = np.concatenate([pausa, paciente, pausa, medico, pausa])
    falas = [
        {"texto": "a b c", "falante": 1, "offset_ms": 500, "duracao_ms": 2500, "palavras": []},
        {"texto": "d e f", "falante": 2, "offset_ms": 3500, "duracao_ms": 2500, "palavras": []},
    ]
    transcricao = {"falas": falas}
    copia = preparar_copia_features(sinal, TAXA, cfg_ingestao.copia_features)

    segmentos = segmentos_da_transcricao(transcricao, medico_falante=2).segmentos
    bio = agregar(medir_segmentos(sinal, copia, segmentos, cfg_vocal))

    assert bio.f0_media_hz == pytest.approx(140.0, abs=4.0)  # sem a voz de 220 Hz do médico


def test_intensidade_acompanha_o_volume_do_original_e_nao_o_da_copia_normalizada(
    cfg_ingestao, cfg_vocal
):
    alto = medir(*fala_com(ParametrosVoz(amplitude=0.3)), cfg_ingestao, cfg_vocal)
    baixo = medir(*fala_com(ParametrosVoz(amplitude=0.1)), cfg_ingestao, cfg_vocal)

    # 0,3 / 0,1 = 3x = 9,5 dB. Se a intensidade viesse da cópia normalizada, a diferença sumiria.
    assert alto.intensidade_db - baixo.intensidade_db == pytest.approx(9.5, abs=1.0)


# --- casos de borda ------------------------------------------------------------------------- #


def test_segmento_curto_demais_e_descartado(cfg_ingestao, cfg_vocal):
    sinal, falas = montar_fala([Frase(0.2, 1, 0.0)], ParametrosVoz())

    with pytest.raises(SemFalaDoPaciente):
        medir(sinal, falas, cfg_ingestao, cfg_vocal)


def test_segmento_silencioso_e_descartado(cfg_ingestao, cfg_vocal):
    sinal = np.zeros(TAXA * 3, np.float32)
    falas = [{"texto": "a", "offset_ms": 0, "duracao_ms": 3000, "palavras": []}]

    with pytest.raises(SemFalaDoPaciente):
        medir(sinal, falas, cfg_ingestao, cfg_vocal)


def test_ruido_de_ambiente_nao_e_tratado_como_fala(cfg_ingestao, cfg_vocal):
    """O limiar de pausa é relativo; sem o piso absoluto, o ruído seria a 'fala' de si mesmo."""
    rng = np.random.default_rng(0)
    sinal = (0.0005 * rng.standard_normal(TAXA * 3)).astype(np.float32)  # cerca de -66 dBFS
    falas = [{"texto": "a", "offset_ms": 0, "duracao_ms": 3000, "palavras": []}]

    with pytest.raises(SemFalaDoPaciente):
        medir(sinal, falas, cfg_ingestao, cfg_vocal)


def test_ruido_sem_voz_nao_gera_nan_so_none(cfg_ingestao, cfg_vocal):
    rng = np.random.default_rng(0)
    sinal = (0.2 * rng.standard_normal(TAXA * 2)).astype(np.float32)  # fala audível, mas sem F0
    falas = [{"texto": "a b", "offset_ms": 0, "duracao_ms": 2000, "palavras": []}]

    bio = medir(sinal, falas, cfg_ingestao, cfg_vocal)

    assert bio.jitter_local_pct is None
    assert bio.shimmer_local_pct is None
    assert bio.hnr_db is None
    assert bio.f0_desvio_st is None
    json.dumps(bio.para_dict(), allow_nan=False)  # NaN seria recusado pelo contrato


def test_sem_transcricao_nao_ha_taxa_de_fala(cfg_ingestao, cfg_vocal):
    from audio.vocal.segmentos import segmentos_por_vad

    sinal, _ = fala_normal()
    copia = preparar_copia_features(sinal, TAXA, cfg_ingestao.copia_features)
    segmentos = segmentos_por_vad(sinal, TAXA, cfg_vocal).segmentos

    bio = agregar(medir_segmentos(sinal, copia, segmentos, cfg_vocal))

    assert bio.palavras is None
    assert bio.taxa_fala_pps is None
    assert bio.taxa_articulacao_pps is None
    assert bio.jitter_local_pct is not None  # as medidas acústicas não dependem da transcrição


def test_agregacao_pondera_jitter_pela_voz_sustentada():
    def medida(indice, vozeado, jitter):
        return MedidaSegmento(
            indice=indice, inicio_s=0.0, fim_s=2.0, palavras=4, pausas_s=0.0, n_pausas=0,
            lacuna_antes_s=0.0, pausa_antes_s=0.0, vozeado_s=vozeado, f0_hz=np.array([100.0]),
            jitter_local_pct=jitter, shimmer_local_pct=None, hnr_db=None, intensidade_db=None)

    bio = agregar([medida(0, 3.0, 0.2), medida(1, 1.0, 1.0), medida(2, 5.0, None)])

    assert bio.jitter_local_pct == pytest.approx((3 * 0.2 + 1 * 1.0) / 4)  # o None não pesa
    assert bio.shimmer_local_pct is None


def test_agregar_sem_segmentos_levanta_erro_claro():
    with pytest.raises(SemFalaDoPaciente, match="nenhum segmento"):
        agregar([])


def test_metricas_da_baseline_e_dicionario_serializavel(cfg_ingestao, cfg_vocal):
    bio = medir(*fala_normal(), cfg_ingestao, cfg_vocal)

    metricas = bio.como_metricas()

    assert set(metricas) == set(cfg_vocal.baseline.metricas)
    json.dumps(bio.para_dict(), allow_nan=False)


def test_versao_do_praat_fica_disponivel_para_o_campo_models():
    assert PRAAT_VERSAO


def test_replace_mantem_a_voz_sintetica_deterministica():
    a = sintetizar_voz(1.0, ParametrosVoz(), semente=3)
    b = sintetizar_voz(1.0, replace(ParametrosVoz()), semente=3)

    assert np.array_equal(a, b)
    assert not np.array_equal(a, sintetizar_voz(1.0, ParametrosVoz(), semente=4))
