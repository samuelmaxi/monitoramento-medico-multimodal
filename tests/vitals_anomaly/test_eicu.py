import logging

import pandas as pd
import pytest

from vitals_anomaly.data.eicu import EICUDataError, eicu_to_canonical, fahrenheit_to_celsius, load_eicu_vitals
from vitals_anomaly.schema import validate_vitals


def _ts(texto):
    return pd.Timestamp(texto, tz="UTC")


@pytest.fixture
def canonico(eicu_tables, vitals_config):
    return eicu_to_canonical(
        eicu_tables["periodic"], eicu_tables["aperiodic"], eicu_tables["patient"], vitals_config.eicu
    )


def _linha(df, patient_id, timestamp):
    sel = df[(df["patient_id"] == patient_id) & (df["timestamp"] == _ts(timestamp))]
    assert len(sel) == 1, f"esperada 1 linha para {patient_id} @ {timestamp}, achadas {len(sel)}"
    return sel.iloc[0]


def test_resultado_segue_o_schema_canonico(canonico):
    validate_vitals(canonico)


def test_ordenado_por_paciente_e_timestamp(canonico):
    esperado = canonico.sort_values(["patient_id", "timestamp"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(canonico, esperado)


def test_ids_e_quantidade_de_linhas(canonico):
    # 100: offsets -5, 5, 10, 20 (o 15 só tem PVC); 200: 0; 300: 30 (só manguito).
    assert canonico["patient_id"].value_counts().to_dict() == {"eicu_100": 4, "eicu_200": 1, "eicu_300": 1}


def test_timestamp_usa_hora_de_admissao_e_offset(canonico):
    # admissão 22:30 + offset -5 min -> 22:25 do dia âncora (offset negativo é mantido)
    linha = _linha(canonico, "eicu_100", "2014-01-01 22:25")
    assert linha["hr"] == 90


def test_offsets_repetidos_viram_a_media_do_instante(canonico):
    assert _linha(canonico, "eicu_100", "2014-01-01 22:35")["hr"] == 83


def test_temperatura_em_fahrenheit_e_convertida(canonico):
    assert _linha(canonico, "eicu_100", "2014-01-01 22:35")["temp"] == pytest.approx(37.0)
    assert _linha(canonico, "eicu_100", "2014-01-01 22:25")["temp"] == pytest.approx(37.2)


def test_pressao_invasiva_tem_prioridade_no_mesmo_instante(canonico):
    linha = _linha(canonico, "eicu_100", "2014-01-01 22:40")
    assert (linha["sbp"], linha["dbp"], linha["bp_source"]) == (120, 70, "invasive")


def test_pressao_nao_invasiva_quando_nao_ha_invasiva(canonico):
    linha = _linha(canonico, "eicu_100", "2014-01-01 22:50")
    assert (linha["sbp"], linha["dbp"], linha["bp_source"]) == (125, 75, "noninvasive")
    assert pd.isna(linha["hr"])


def test_internacao_so_com_manguito_e_mantida(canonico):
    linha = _linha(canonico, "eicu_300", "2014-01-01 00:30")
    assert linha["sbp"] == 110


def test_sem_pressao_bp_source_e_nulo(canonico):
    assert pd.isna(_linha(canonico, "eicu_200", "2014-01-01 08:00")["bp_source"])


def test_sem_preenchimento_de_faltantes(canonico):
    # A FR da internação 100 só existe nos offsets 5 e 10; os demais ficam NaN.
    rr = canonico.loc[canonico["patient_id"] == "eicu_100", "rr"].tolist()
    assert pd.isna(rr[0]) and rr[1:3] == [18, 16] and pd.isna(rr[3])


def test_internacao_sem_tabela_patient_e_descartada_com_aviso(eicu_tables, vitals_config, caplog):
    with caplog.at_level(logging.WARNING):
        df = eicu_to_canonical(
            eicu_tables["periodic"], eicu_tables["aperiodic"], eicu_tables["patient"], vitals_config.eicu
        )
    assert "eicu_400" not in set(df["patient_id"])
    assert "sem hora de admissão" in caplog.text


def test_contexto_nao_disponivel_no_eicu_fica_nulo(canonico):
    assert canonico["o2_supplemental"].isna().all()
    assert canonico["consciousness"].isna().all()


@pytest.mark.parametrize("ids", [[200, 300], ["eicu_200", "eicu_300"]])
def test_filtro_por_patient_ids(eicu_tables, vitals_config, ids):
    df = eicu_to_canonical(
        eicu_tables["periodic"], eicu_tables["aperiodic"], eicu_tables["patient"], vitals_config.eicu,
        patient_ids=ids,
    )
    assert set(df["patient_id"]) == {"eicu_200", "eicu_300"}


def test_max_patients_pega_os_primeiros_ids(eicu_tables, vitals_config):
    df = eicu_to_canonical(
        eicu_tables["periodic"], eicu_tables["aperiodic"], eicu_tables["patient"], vitals_config.eicu,
        max_patients=2,
    )
    assert set(df["patient_id"]) == {"eicu_100", "eicu_200"}


def test_ingestao_e_causal_dados_futuros_nao_alteram_o_passado(eicu_tables, vitals_config, canonico):
    """Cortar as tabelas em um offset não muda nenhuma linha anterior ao corte."""
    corte = 10
    truncadas = {
        k: (v[v["observationoffset"] <= corte] if "observationoffset" in v else v) for k, v in eicu_tables.items()
    }
    parcial = eicu_to_canonical(truncadas["periodic"], truncadas["aperiodic"], truncadas["patient"], vitals_config.eicu)
    admissao = {"eicu_100": "22:30", "eicu_200": "08:00", "eicu_300": "00:00"}
    limite = canonico["patient_id"].map(lambda p: _ts(f"2014-01-01 {admissao[p]}") + pd.Timedelta(minutes=corte))
    esperado = canonico[canonico["timestamp"] <= limite].reset_index(drop=True)
    pd.testing.assert_frame_equal(parcial, esperado)


def test_fahrenheit_to_celsius_so_converte_a_faixa_configurada():
    temp = pd.Series([98.6, 37.0, 200.0, float("nan")])
    out = fahrenheit_to_celsius(temp, (86.0, 113.0))
    assert out.iloc[0] == pytest.approx(37.0)
    assert out.iloc[1:3].tolist() == [37.0, 200.0]
    assert pd.isna(out.iloc[3])


def test_load_eicu_vitals_le_os_csv_gzip(eicu_dir, vitals_config, canonico):
    df = load_eicu_vitals(vitals_config.eicu, data_dir=eicu_dir)
    pd.testing.assert_frame_equal(df, canonico)


def test_arquivo_ausente_orienta_a_rodar_o_download(tmp_path, vitals_config):
    with pytest.raises(EICUDataError, match="download_data.py"):
        load_eicu_vitals(vitals_config.eicu, data_dir=tmp_path)


def test_coluna_ausente_no_csv_gera_erro(eicu_dir, eicu_tables, vitals_config):
    eicu_tables["periodic"].drop(columns=["heartrate"]).to_csv(
        eicu_dir / vitals_config.eicu.files.periodic, index=False, compression="gzip"
    )
    with pytest.raises(EICUDataError, match="heartrate"):
        load_eicu_vitals(vitals_config.eicu, data_dir=eicu_dir)
