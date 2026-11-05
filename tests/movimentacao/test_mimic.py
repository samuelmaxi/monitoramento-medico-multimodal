import pandas as pd
import pytest

from movimentacao.mimic import observacoes_de_dataframe
from movimentacao.modelos import Posicao as P


def _df(*linhas):
    return pd.DataFrame(
        [
            {"subject_id": s, "hadm_id": h, "stay_id": st, "charttime": t, "itemid": i, "value": v}
            for s, h, st, t, i, v in linhas
        ]
    )


def _uma(item, valor):
    serie = observacoes_de_dataframe(_df((1, 10, 100, "2150-01-01 08:00:00", item, valor)))
    return serie[("1", "10", "100")]


@pytest.mark.parametrize(
    ("valor", "posicao"),
    [
        ("Supine", P.SUPINO),
        ("Left Side", P.LATERAL_ESQ),
        ("Right Side", P.LATERAL_DIR),
        ("Prone", P.PRONO),
        ("Sitting", P.SENTADO),
        ("Rotating", P.ROTATIVO),
        ("Reverse T-berg", P.SUPINO),
    ],
)
def test_mapeia_position(valor, posicao):
    [o] = _uma(224093, valor)
    assert o.posicao is posicao
    assert not o.mudanca
    assert o.fonte == "mimic_position"
    assert o.instante.isoformat() == "2150-01-01T08:00:00+00:00"


def test_turn_com_lado_e_posicao():
    [o] = _uma(224082, "Back")
    assert o.posicao is P.SUPINO


@pytest.mark.parametrize("valor", ["Side to Side", "Turns by Self", "Logroll", "Logroll x4 Assist"])
def test_turn_sem_lado_e_mudanca(valor):
    [o] = _uma(224082, valor)
    assert o.posicao is P.DESCONHECIDA
    assert o.mudanca


@pytest.mark.parametrize("valor", ["Chair", "Commode", "Ambulate"])
def test_activity_fora_do_leito(valor):
    [o] = _uma(224084, valor)
    assert o.posicao is P.FORA_DO_LEITO


@pytest.mark.parametrize("valor", ["Dangle", "Bed as Chair"])
def test_activity_sentado(valor):
    [o] = _uma(224084, valor)
    assert o.posicao is P.SENTADO


def test_valor_desconhecido_ignorado():
    serie = observacoes_de_dataframe(_df((1, 10, 100, "2150-01-01 08:00:00", 224084, "Bedrest")))
    assert serie == {}


def test_espacos_e_item_estranho_ignorados():
    serie = observacoes_de_dataframe(
        _df(
            (1, 10, 100, "2150-01-01 08:00:00", 224093, " Supine "),
            (1, 10, 100, "2150-01-01 09:00:00", 220045, "80"),
        )
    )
    assert [o.posicao for o in serie[("1", "10", "100")]] == [P.SUPINO]


def test_agrupa_por_stay_e_ordena():
    serie = observacoes_de_dataframe(
        _df(
            (1, 10, 100, "2150-01-01 10:00:00", 224093, "Left Side"),
            (1, 10, 100, "2150-01-01 08:00:00", 224093, "Supine"),
            (2, 20, 200, "2150-02-01 08:00:00", 224093, "Supine"),
        )
    )
    assert set(serie) == {("1", "10", "100"), ("2", "20", "200")}
    assert [o.posicao for o in serie[("1", "10", "100")]] == [P.SUPINO, P.LATERAL_ESQ]
