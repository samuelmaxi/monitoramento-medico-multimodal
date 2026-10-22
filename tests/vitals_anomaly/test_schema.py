from datetime import UTC, datetime, timedelta, timezone

import pandas as pd
import pytest
from pydantic import ValidationError

from vitals_anomaly.schema import (
    CANONICAL_COLUMNS,
    CANONICAL_DTYPES,
    Consciousness,
    SchemaError,
    VitalSample,
    coerce_vitals,
    empty_vitals_frame,
    frame_to_samples,
    samples_to_frame,
    validate_vitals,
)

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _amostra(**kw):
    base = {"patient_id": "p1", "timestamp": T0, "hr": 80, "sbp": 120, "dbp": 80, "spo2": 97}
    return VitalSample(**(base | kw))


def _frame_minimo(**colunas):
    base = {
        "patient_id": ["p1", "p1"],
        "timestamp": [T0, T0 + timedelta(minutes=5)],
        "hr": [80, 81],
        "sbp": [120, 121],
        "dbp": [80, 79],
        "spo2": [97, 98],
    }
    return pd.DataFrame(base | colunas)


def test_vitalsample_aceita_opcionais_ausentes():
    s = _amostra()
    assert s.rr is None and s.temp is None and s.consciousness is None


def test_vitalsample_converte_timestamp_para_utc():
    s = _amostra(timestamp=datetime(2026, 1, 1, 9, 0, tzinfo=timezone(timedelta(hours=-3))))
    assert s.timestamp == T0 and s.timestamp.tzinfo == UTC


def test_vitalsample_rejeita_timestamp_sem_fuso():
    with pytest.raises(ValidationError):
        _amostra(timestamp=datetime(2026, 1, 1, 12, 0))


@pytest.mark.parametrize("campo,valor", [("spo2", 101), ("hr", -1), ("temp", 0), ("consciousness", "X")])
def test_vitalsample_rejeita_valores_fisicamente_invalidos(campo, valor):
    with pytest.raises(ValidationError):
        _amostra(**{campo: valor})


def test_vitalsample_rejeita_campo_desconhecido():
    with pytest.raises(ValidationError):
        _amostra(map=90)


def test_vitalsample_aceita_acvpu_como_texto():
    assert _amostra(consciousness="V").consciousness is Consciousness.VOICE


def test_empty_frame_tem_schema_canonico():
    validate_vitals(empty_vitals_frame())


def test_coerce_completa_opcionais_e_aplica_dtypes():
    df = coerce_vitals(_frame_minimo())
    assert tuple(df.columns) == CANONICAL_COLUMNS
    assert {c: df[c].dtype for c in df.columns} == CANONICAL_DTYPES
    assert df["rr"].isna().all() and df["consciousness"].isna().all()
    validate_vitals(df)


def test_coerce_ordena_por_paciente_e_timestamp():
    df = _frame_minimo(patient_id=["p2", "p1"], timestamp=[T0, T0 + timedelta(minutes=5)])
    assert coerce_vitals(df)["patient_id"].tolist() == ["p1", "p2"]


def test_coerce_exige_colunas_obrigatorias():
    with pytest.raises(SchemaError, match="spo2"):
        coerce_vitals(_frame_minimo().drop(columns=["spo2"]))


def test_coerce_rejeita_colunas_extras():
    with pytest.raises(SchemaError, match="cvp"):
        coerce_vitals(_frame_minimo(cvp=[5, 6]))


def test_validate_rejeita_desordenado():
    df = coerce_vitals(_frame_minimo()).iloc[::-1].reset_index(drop=True)
    with pytest.raises(SchemaError, match="ordenado"):
        validate_vitals(df)


def test_validate_rejeita_duplicatas():
    df = coerce_vitals(_frame_minimo(timestamp=[T0, T0]))
    with pytest.raises(SchemaError, match="duplicadas"):
        validate_vitals(df)


def test_validate_rejeita_dtype_errado():
    df = coerce_vitals(_frame_minimo())
    df["hr"] = df["hr"].astype("int64")
    with pytest.raises(SchemaError, match="hr"):
        validate_vitals(df)


def test_validate_rejeita_spo2_acima_de_100():
    df = coerce_vitals(_frame_minimo(spo2=[97, 104]))
    with pytest.raises(SchemaError, match="spo2"):
        validate_vitals(df)


def test_ida_e_volta_entre_amostras_e_dataframe():
    amostras = [
        _amostra(),
        _amostra(timestamp=T0 + timedelta(minutes=5), rr=18, temp=37.1, o2_supplemental=True, consciousness="A"),
        _amostra(patient_id="p2", hr=None, bp_source="noninvasive"),
    ]
    df = samples_to_frame(amostras)
    validate_vitals(df)
    assert frame_to_samples(df) == amostras


def test_samples_to_frame_vazio():
    assert samples_to_frame([]).empty
