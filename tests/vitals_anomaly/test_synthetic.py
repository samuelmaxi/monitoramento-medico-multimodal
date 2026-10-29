import dataclasses

import numpy as np
import pandas as pd
import pytest

from vitals_anomaly.data.synthetic import generate_synthetic_vitals
from vitals_anomaly.schema import REQUIRED_VITALS, validate_vitals


@pytest.fixture(scope="module")
def sintetico(vitals_config):
    return generate_synthetic_vitals(vitals_config.synthetic, n_patients=6, duration_hours=48)


@pytest.fixture(scope="module")
def sem_faltantes(vitals_config):
    cfg = dataclasses.replace(vitals_config.synthetic, missing_rate=0.0)
    return generate_synthetic_vitals(cfg, n_patients=6, duration_hours=48)


def test_segue_o_schema_canonico(sintetico):
    validate_vitals(sintetico)


def test_quantidade_de_linhas_e_frequencia(sintetico, vitals_config):
    passo = pd.Timedelta(minutes=vitals_config.synthetic.freq_minutes)
    assert len(sintetico) == 6 * int(pd.Timedelta(hours=48) / passo)
    deltas = sintetico.groupby("patient_id")["timestamp"].diff().dropna()
    assert (deltas == passo).all()


def test_mesma_seed_gera_o_mesmo_dataframe(vitals_config, sintetico):
    de_novo = generate_synthetic_vitals(vitals_config.synthetic, n_patients=6, duration_hours=48)
    pd.testing.assert_frame_equal(sintetico, de_novo)


def test_seed_diferente_gera_valores_diferentes(vitals_config, sintetico):
    outro = generate_synthetic_vitals(vitals_config.synthetic, n_patients=6, duration_hours=48, seed=7)
    assert not np.allclose(sintetico["hr"].fillna(0), outro["hr"].fillna(0))


def test_paciente_nao_depende_de_quantos_sao_gerados(vitals_config, sintetico):
    um = generate_synthetic_vitals(vitals_config.synthetic, n_patients=1, duration_hours=48)
    pd.testing.assert_frame_equal(um, sintetico[sintetico["patient_id"] == "syn_0001"].reset_index(drop=True))


def test_baseline_individual_difere_entre_pacientes(sem_faltantes):
    medias = sem_faltantes.groupby("patient_id")["hr"].mean()
    # Desvio entre pacientes bem maior que o erro da média de cada um.
    assert medias.std() > 3


def test_ritmo_circadiano_fc_maior_a_tarde_que_de_madrugada(vitals_config):
    cfg = dataclasses.replace(vitals_config.synthetic, missing_rate=0.0)
    df = generate_synthetic_vitals(cfg, n_patients=20, duration_hours=72)
    hora = df["timestamp"].dt.hour
    pico = cfg.circadian_peak_hour
    perto_do_pico = df.loc[(hora - pico).abs() <= 2, "hr"].mean()
    perto_do_vale = df.loc[((hora - (pico + 12) % 24).abs() <= 2), "hr"].mean()
    assert perto_do_pico - perto_do_vale > cfg.vitals["hr"].circadian_amplitude


def test_valores_dentro_dos_limites_e_na_resolucao(sem_faltantes, vitals_config):
    for nome, cfg in vitals_config.synthetic.vitals.items():
        serie = sem_faltantes[nome]
        assert serie.between(cfg.min, cfg.max).all(), nome
        np.testing.assert_allclose(serie / cfg.resolution, np.round(serie / cfg.resolution), atol=1e-6)


def test_diastolica_menor_que_sistolica(sem_faltantes):
    assert (sem_faltantes["dbp"] < sem_faltantes["sbp"]).all()


def test_sistolica_e_diastolica_correlacionadas(vitals_config):
    cfg = dataclasses.replace(vitals_config.synthetic, missing_rate=0.0)
    df = generate_synthetic_vitals(cfg, n_patients=40, duration_hours=6)
    assert df["sbp"].corr(df["dbp"]) > 0.3


def test_ruido_e_autocorrelacionado(sem_faltantes):
    hr = sem_faltantes.loc[sem_faltantes["patient_id"] == "syn_0001", "hr"]
    assert hr.autocorr(lag=1) > 0.5


def test_taxa_de_faltantes_aproximada(sintetico, vitals_config):
    taxa = sintetico[list(REQUIRED_VITALS)].isna().mean()
    assert np.allclose(taxa, vitals_config.synthetic.missing_rate, atol=0.01)


def test_sem_faltantes_quando_missing_rate_zero(sem_faltantes):
    assert not sem_faltantes[list(REQUIRED_VITALS) + ["rr", "temp"]].isna().any().any()


def test_contexto_preenchido(sintetico):
    assert (sintetico["consciousness"] == "A").all()
    assert sintetico["o2_supplemental"].notna().all()
    por_paciente = sintetico.groupby("patient_id")["o2_supplemental"].nunique()
    assert (por_paciente == 1).all()


@pytest.mark.parametrize("kwargs", [{"n_patients": 0}, {"duration_hours": -1}])
def test_parametros_invalidos(vitals_config, kwargs):
    with pytest.raises(ValueError):
        generate_synthetic_vitals(vitals_config.synthetic, **kwargs)
