"""NEWS2: limites de cada faixa da tabela de referência da US15 e gatilhos."""

import pytest

from sinais import news2


@pytest.mark.parametrize(
    ("parametro", "valor", "pontos"),
    [
        ("fr", 8, 3),
        ("fr", 9, 1),
        ("fr", 11, 1),
        ("fr", 12, 0),
        ("fr", 20, 0),
        ("fr", 21, 2),
        ("fr", 24, 2),
        ("fr", 25, 3),
        ("spo2", 91, 3),
        ("spo2", 92, 2),
        ("spo2", 93, 2),
        ("spo2", 94, 1),
        ("spo2", 95, 1),
        ("spo2", 96, 0),
        ("pas", 90, 3),
        ("pas", 91, 2),
        ("pas", 100, 2),
        ("pas", 101, 1),
        ("pas", 110, 1),
        ("pas", 111, 0),
        ("pas", 219, 0),
        ("pas", 220, 3),
        ("fc", 40, 3),
        ("fc", 41, 1),
        ("fc", 50, 1),
        ("fc", 51, 0),
        ("fc", 90, 0),
        ("fc", 91, 1),
        ("fc", 110, 1),
        ("fc", 111, 2),
        ("fc", 130, 2),
        ("fc", 131, 3),
        ("temp", 35.0, 3),
        ("temp", 35.1, 1),
        ("temp", 36.0, 1),
        ("temp", 36.1, 0),
        ("temp", 38.0, 0),
        ("temp", 38.1, 1),
        ("temp", 39.0, 1),
        ("temp", 39.1, 2),
        ("o2_suplementar", 1, 2),
        ("o2_suplementar", 0, 0),
        ("alerta", 1, 0),
        ("alerta", 0, 3),
    ],
)
def test_pontos_por_parametro(parametro, valor, pontos):
    assert news2.pontos_parametro(parametro, valor) == pontos


NORMAL = {
    "fr": 16,
    "spo2": 98,
    "pas": 120,
    "fc": 75,
    "temp": 37.0,
    "o2_suplementar": 0,
    "alerta": 1,
}


@pytest.mark.parametrize(
    ("alteracoes", "total", "gatilho"),
    [
        ({}, 0, None),
        ({"fc": 95, "spo2": 94, "fr": 22}, 4, None),
        ({"fc": 115, "spo2": 94, "fr": 22}, 5, news2.URGENTE),
        ({"fr": 25}, 3, news2.URGENTE),  # 3 em parâmetro único
        ({"fc": 115, "spo2": 93, "fr": 22}, 6, news2.URGENTE),
        ({"fc": 115, "spo2": 93, "fr": 22, "temp": 38.5}, 7, news2.EMERGENCIA),
    ],
)
def test_total_e_gatilho(alteracoes, total, gatilho):
    resultado = news2.calcular(NORMAL | alteracoes)
    assert (resultado.total, resultado.gatilho) == (total, gatilho)


def test_parametro_ausente_nao_pontua():
    resultado = news2.calcular({"fc": 135})
    assert resultado.total == 3
    assert "spo2" in resultado.ausentes and "fc" not in resultado.ausentes
