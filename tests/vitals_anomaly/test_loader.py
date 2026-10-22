import pandas as pd
import pytest
import yaml

from vitals_anomaly import load_vitals
from vitals_anomaly.config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from vitals_anomaly.schema import CANONICAL_COLUMNS, CANONICAL_DTYPES, validate_vitals


def _confere_schema(df):
    assert tuple(df.columns) == CANONICAL_COLUMNS
    assert {c: df[c].dtype for c in df.columns} == CANONICAL_DTYPES
    assert df[["patient_id", "timestamp"]].equals(
        df[["patient_id", "timestamp"]].sort_values(["patient_id", "timestamp"]).reset_index(drop=True)
    )
    validate_vitals(df)


def test_load_vitals_sintetico(vitals_config):
    df = load_vitals("synthetic", config=vitals_config, max_patients=3, duration_hours=2)
    _confere_schema(df)
    assert df["patient_id"].nunique() == 3


def test_load_vitals_eicu(eicu_dir, vitals_config):
    df = load_vitals("eicu", config=vitals_config, data_dir=eicu_dir)
    _confere_schema(df)
    assert set(df["patient_id"]) == {"eicu_100", "eicu_200", "eicu_300"}


def test_load_vitals_aceita_caminho_do_config(tmp_path):
    dados = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    dados["sources"]["synthetic"]["n_patients"] = 2
    dados["sources"]["synthetic"]["duration_hours"] = 1
    caminho = tmp_path / "vitals.yaml"
    caminho.write_text(yaml.safe_dump(dados), encoding="utf-8")
    assert load_vitals("synthetic", config=caminho)["patient_id"].nunique() == 2


def test_load_vitals_source_desconhecida(vitals_config):
    with pytest.raises(ValueError, match="source desconhecida"):
        load_vitals("mimic", config=vitals_config)


@pytest.mark.parametrize(
    "source,kwargs",
    [("eicu", {"seed": 1}), ("eicu", {"duration_hours": 1}), ("synthetic", {"patient_ids": [1]})],
)
def test_load_vitals_rejeita_argumentos_da_outra_fonte(vitals_config, source, kwargs):
    with pytest.raises(ValueError, match="só valem"):
        load_vitals(source, config=vitals_config, **kwargs)


def test_config_padrao_carrega():
    cfg = load_config()
    assert cfg.synthetic.start == pd.Timestamp("2026-01-01T00:00:00Z")
    assert set(cfg.synthetic.vitals) >= {"hr", "sbp", "dbp", "spo2"}


@pytest.mark.parametrize(
    "caminho,valor,mensagem",
    [
        (("sources", "synthetic", "missing_rate"), 1.5, "missing_rate"),
        (("sources", "synthetic", "vitals", "hr", "ar_phi"), 1.0, "ar_phi"),
        (("sources", "eicu", "fahrenheit_range"), [113, 86], "fahrenheit_range"),
        (("sources", "synthetic", "n_patients"), 0, "n_patients"),
    ],
)
def test_config_invalido(tmp_path, caminho, valor, mensagem):
    dados = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    alvo = dados
    for chave in caminho[:-1]:
        alvo = alvo[chave]
    alvo[caminho[-1]] = valor
    arquivo = tmp_path / "vitals.yaml"
    arquivo.write_text(yaml.safe_dump(dados), encoding="utf-8")
    with pytest.raises(ConfigError, match=mensagem):
        load_config(arquivo)


def test_config_sem_chave_obrigatoria(tmp_path):
    dados = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    del dados["sources"]["eicu"]["anchor_date"]
    arquivo = tmp_path / "vitals.yaml"
    arquivo.write_text(yaml.safe_dump(dados), encoding="utf-8")
    with pytest.raises(ConfigError, match="sources.eicu.anchor_date"):
        load_config(arquivo)


def test_config_sintetico_sem_sinal_obrigatorio(tmp_path):
    dados = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    del dados["sources"]["synthetic"]["vitals"]["spo2"]
    arquivo = tmp_path / "vitals.yaml"
    arquivo.write_text(yaml.safe_dump(dados), encoding="utf-8")
    with pytest.raises(ConfigError, match="spo2"):
        load_config(arquivo)
