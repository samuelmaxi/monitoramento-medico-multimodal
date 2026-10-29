"""Integração com o eICU Demo real e completo (todas as internações).

Pulado quando os dados não foram baixados (python scripts/download_data.py).
Compara a saída de ``load_vitals("eicu")`` com os CSV crus lidos diretamente.
"""

import pandas as pd
import pytest

from vitals_anomaly import load_vitals
from vitals_anomaly.config import load_config
from vitals_anomaly.schema import validate_vitals

CFG = load_config().eicu
HA_DADOS = all((CFG.data_dir / f).exists() for f in (CFG.files.periodic, CFG.files.aperiodic, CFG.files.patient))

pytestmark = pytest.mark.skipif(not HA_DADOS, reason="eICU Demo não baixado (python scripts/download_data.py)")

STAY, OFFSET = "patientunitstayid", "observationoffset"
CHAVE = [STAY, OFFSET]
# Internações cuja temperatura vem em °F no cru (levantadas na tabela vitalPeriodic).
STAYS_EM_FAHRENHEIT = {1195714, 1578108, 2689235, 2873463}


@pytest.fixture(scope="module")
def saida():
    return load_vitals("eicu")


@pytest.fixture(scope="module")
def cru():
    periodic = pd.read_csv(
        CFG.data_dir / CFG.files.periodic,
        usecols=[*CHAVE, "heartrate", "sao2", "respiration", "temperature", "systemicsystolic", "systemicdiastolic"],
    )
    aperiodic = pd.read_csv(
        CFG.data_dir / CFG.files.aperiodic, usecols=[*CHAVE, "noninvasivesystolic", "noninvasivediastolic"]
    )
    patient = pd.read_csv(CFG.data_dir / CFG.files.patient, usecols=[STAY, "unitadmittime24"])
    return {"periodic": periodic, "aperiodic": aperiodic, "patient": patient}


def _stay(patient_ids: pd.Series) -> pd.Series:
    return patient_ids.str.removeprefix(CFG.patient_id_prefix).astype(int)


def _contagem_saida(saida, coluna):
    return saida.dropna(subset=[coluna]).groupby(_stay(saida["patient_id"])).size()


def _timestamp(cru, stay, offset):
    admissao = cru["patient"].set_index(STAY).loc[stay, "unitadmittime24"]
    return pd.Timestamp(CFG.anchor_date, tz="UTC") + pd.to_timedelta(admissao) + pd.Timedelta(minutes=int(offset))


def test_dataset_completo_segue_o_schema(saida):
    validate_vitals(saida)


def test_todas_as_internacoes_com_sinal_estao_na_saida(saida, cru):
    p = cru["periodic"]
    com_sinal_p = p.loc[p.drop(columns=CHAVE).notna().any(axis=1), STAY]
    a = cru["aperiodic"]
    com_sinal_a = a.loc[a[["noninvasivesystolic", "noninvasivediastolic"]].notna().any(axis=1), STAY]
    esperadas = set(com_sinal_p) | set(com_sinal_a)
    assert set(_stay(saida["patient_id"])) == esperadas
    assert len(esperadas) == 2380


@pytest.mark.parametrize(
    "coluna_cru,coluna", [("heartrate", "hr"), ("sao2", "spo2"), ("respiration", "rr"), ("temperature", "temp")]
)
def test_contagem_por_sinal_igual_ao_cru_em_todas_as_internacoes(saida, cru, coluna_cru, coluna):
    esperado = cru["periodic"].dropna(subset=[coluna_cru]).drop_duplicates(CHAVE).groupby(STAY).size()
    obtido = _contagem_saida(saida, coluna)
    esperado, obtido = esperado.align(obtido, fill_value=0)
    pd.testing.assert_series_equal(obtido, esperado, check_names=False)


def test_instantes_com_pressao_iguais_ao_cru(saida, cru):
    inv = cru["periodic"].dropna(subset=["systemicsystolic", "systemicdiastolic"], how="all")[CHAVE]
    ni = cru["aperiodic"].dropna(subset=["noninvasivesystolic", "noninvasivediastolic"], how="all")[CHAVE]
    esperado = pd.concat([inv, ni]).drop_duplicates().groupby(STAY).size()
    obtido = _contagem_saida(saida, "bp_source")
    esperado, obtido = esperado.align(obtido, fill_value=0)
    pd.testing.assert_series_equal(obtido, esperado, check_names=False)


def test_temperatura_extraida_nas_170_internacoes_que_a_tem(saida, cru):
    temp = cru["periodic"].dropna(subset=["temperature"])
    assert temp[STAY].nunique() == 170
    assert set(_stay(saida.loc[saida["temp"].notna(), "patient_id"])) == set(temp[STAY])
    assert saida["temp"].notna().sum() == temp.drop_duplicates(CHAVE).shape[0] == 112_820


def test_temperatura_em_fahrenheit_convertida_para_celsius(saida, cru):
    temp = cru["periodic"].dropna(subset=["temperature"])
    lo, hi = CFG.fahrenheit_range
    em_f = temp[temp["temperature"].between(lo, hi)]
    assert set(em_f[STAY]) == STAYS_EM_FAHRENHEIT
    assert len(em_f) == 2912

    em_f = em_f.assign(
        patient_id=CFG.patient_id_prefix + em_f[STAY].astype(str),
        timestamp=[_timestamp(cru, s, o) for s, o in zip(em_f[STAY], em_f[OFFSET])],
    )
    junto = em_f.merge(saida[["patient_id", "timestamp", "temp"]], on=["patient_id", "timestamp"])
    assert len(junto) == len(em_f)
    pd.testing.assert_series_equal(
        junto["temp"], (junto["temperature"] - 32) * 5 / 9, check_names=False, rtol=0, atol=1e-9
    )
    assert saida["temp"].max() <= 45


def test_serie_com_temperatura_eicu_1034813(saida, cru):
    serie = saida[saida["patient_id"] == "eicu_1034813"]
    com_temp = serie[serie["temp"].notna()]
    assert len(com_temp) == 295
    # A temperatura dessa internação só começa no offset 2135 min (~35 h).
    assert com_temp["timestamp"].iloc[0] == _timestamp(cru, 1034813, 2135)
    assert com_temp["temp"].iloc[0] == pytest.approx(36.7)
    assert com_temp["temp"].between(30, 43).all()


def test_pressao_invasiva_tem_prioridade_sobre_manguito_no_mesmo_instante(saida, cru):
    inv = cru["periodic"].dropna(subset=["systemicsystolic", "systemicdiastolic"], how="all")
    inv = inv.groupby(CHAVE, as_index=False)[["systemicsystolic", "systemicdiastolic"]].mean()
    ni = cru["aperiodic"].dropna(subset=["noninvasivesystolic", "noninvasivediastolic"], how="all")[CHAVE]
    ambos = inv.merge(ni.drop_duplicates(), on=CHAVE)
    assert len(ambos) > 0
    ambos = ambos.assign(
        patient_id=CFG.patient_id_prefix + ambos[STAY].astype(str),
        timestamp=[_timestamp(cru, s, o) for s, o in zip(ambos[STAY], ambos[OFFSET])],
    )
    junto = ambos.merge(saida, on=["patient_id", "timestamp"])
    assert len(junto) == len(ambos)
    assert (junto["bp_source"] == "invasive").all()
    pd.testing.assert_series_equal(junto["sbp"], junto["systemicsystolic"], check_names=False)
    pd.testing.assert_series_equal(junto["dbp"], junto["systemicdiastolic"], check_names=False)
