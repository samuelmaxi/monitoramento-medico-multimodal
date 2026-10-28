import pytest

from prescricoes.normalizacao import (
    AGENDADA,
    CONFORME_ORIENTACAO,
    DOSE_UNICA,
    PRN,
    TITULAVEL,
    converter,
    grupo_de_via,
    interpretar_dose,
    tipo_de_frequencia,
)
from prescricoes.principios import principios_ativos


@pytest.mark.parametrize(
    ("valor", "unidade", "esperado"),
    [
        ("500", "mcg", (0.5, 0.5, "mg")),
        ("1", "g", (1000, 1000, "mg")),
        ("2", "gm", (2000, 2000, "mg")),
        ("0.5", "L", (500, 500, "mL")),
        ("25,000", "UNIT", (25000, 25000, "UNIT")),
        ("2-4", "mg", (2, 4, "mg")),
        (".5-1", "mg", (0.5, 1, "mg")),
        ("40", "mEq", (40, 40, "mEq")),
    ],
)
def test_dose_normalizada_antes_de_comparar(valor, unidade, esperado):
    dose = interpretar_dose(valor, unidade)
    assert (dose.minimo, dose.maximo, dose.unidade) == pytest.approx(esperado)


def test_dose_faixa_usa_o_maximo():
    dose = interpretar_dose("325-650", "mg")
    assert dose.faixa and dose.valor == 650


@pytest.mark.parametrize("valor", ["", None, "1 tab", "MD to order", float("nan")])
def test_dose_nao_numerica_vira_none(valor):
    assert interpretar_dose(valor, "mg") is None


def test_converter_respeita_dimensao():
    assert converter(1500, "mcg", "g") == pytest.approx(0.0015)
    assert converter(1, "L", "mL") == 1000
    assert converter(1, "mg", "mL") is None


@pytest.mark.parametrize(
    ("via", "grupo"),
    [("PO/NG", "enteral"), ("IV DRIP", "IV"), ("SUBCUT", "SC"), ("TD", "transdermica"), ("BOTH EYES", "topica")],
)
def test_grupo_de_via(via, grupo):
    assert grupo_de_via(via) == grupo


@pytest.mark.parametrize(
    ("freq", "tipo"),
    [
        ("Q6H:PRN", PRN),
        ("ONCE", DOSE_UNICA),
        ("NOW X1", DOSE_UNICA),
        ("TITRATE TO RASS", TITULAVEL),
        ("INFUSION", TITULAVEL),
        ("ASDIR", CONFORME_ORIENTACAO),
        ("BID", AGENDADA),
        ("3X/WEEK", AGENDADA),
        ("DAILY16", AGENDADA),
    ],
)
def test_tipo_de_frequencia(freq, tipo):
    assert tipo_de_frequencia(freq) == tipo


def test_escala_movel_nao_tem_dose_fixa():
    assert tipo_de_frequencia("QACHS", escala_movel=True) == CONFORME_ORIENTACAO


@pytest.mark.parametrize(
    ("nome", "principios"),
    [
        ("Metoprolol Succinate XL", ("metoprolol",)),
        ("Metoprolol Tartrate", ("metoprolol",)),
        ("HYDROmorphone (Dilaudid)", ("hydromorphone",)),
        ("OxyCODONE--Acetaminophen (5mg-325mg)", ("acetaminophen", "oxycodone")),
        ("Acetaminophen w/Codeine", ("acetaminophen", "codeine")),
        ("Losartan Potassium", ("losartan",)),
        ("Potassium Chloride Replacement (Critical Care and Oncology) ", ("potassium chloride",)),
        ("Calcium Gluconate", ("calcium gluconate",)),
        ("Divalproex (EXTended Release)", ("valproate",)),
        ("PHENObarbital - ICU Alcohol Withdrawal (Initial Load / Rescue Dose)", ("phenobarbital",)),
        ("Insulin Human Regular", ("insulin regular",)),
        ("Insulin Glargine  (CVICU Protocol)", ("insulin glargine",)),
        ("Tricor", ("fenofibrate",)),
        ("Clonidine Patch 0.2 mg/24 hr", ("clonidine",)),
        ("*NF* Ertapenem Sodium", ("ertapenem",)),
    ],
)
def test_principio_ativo(nome, principios):
    assert principios_ativos(nome) == principios
