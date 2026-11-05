from datetime import timedelta

import pytest

from contratos import Severidade
from movimentacao.avaliacao import (
    ClipeRotulado,
    avaliar_casos,
    avaliar_clipes,
    ler_rotulos,
    particao,
    quedas_do_clipe,
    wilson,
)
from movimentacao.injecao import Caso, gerar_casos
from movimentacao.modelos import Observacao
from movimentacao.modelos import Posicao as P
from movimentacao.video import CaixasDoVideo

from .conftest import instante


def _serie_real(horas: int = 40):
    """Paciente com troca de posição a cada 2 h e registro a cada hora, por ``horas``."""
    posicoes = [P.SUPINO, P.LATERAL_ESQ, P.LATERAL_DIR]
    return [
        Observacao(instante("00:00") + timedelta(hours=h), posicoes[(h // 2) % 3], "mimic_position")
        for h in range(horas)
    ]


@pytest.fixture(scope="module")
def series():
    return {("1", "10", "100"): _serie_real(), ("2", "20", "200"): _serie_real(60)}


def test_casos_cobrem_todos_os_cenarios(cfg, series):
    casos = gerar_casos(series, cfg, seed=1, n_por_tipo=3)
    cenarios = {c.cenario for c in casos}
    assert cenarios == {
        "queda_dia", "queda_noite", "saida_noite", "saida_dia_controle",
        "imobilidade", "imobilidade_controle", "agitacao", "reducao",
    }
    assert all(len([c for c in casos if c.cenario == k]) == 3 for k in cenarios)


def test_casos_reprodutiveis_por_seed(cfg, series):
    a = gerar_casos(series, cfg, seed=7, n_por_tipo=2)
    b = gerar_casos(series, cfg, seed=7, n_por_tipo=2)
    assert [(c.cenario, c.inicio) for c in a] == [(c.cenario, c.inicio) for c in b]


def test_casos_injetados_sao_detectados(cfg, series):
    casos = gerar_casos(series, cfg, seed=3, n_por_tipo=4)
    resultado = avaliar_casos(casos, cfg)
    for tipo in ("queda", "saida_do_leito", "imobilidade_prolongada", "agitacao_subita", "reducao_mobilidade"):
        assert resultado[tipo]["total"].recall == 1.0, (tipo, resultado[tipo]["total"].perdidas)


def test_controles_nao_disparam(cfg, series):
    casos = gerar_casos(series, cfg, seed=3, n_por_tipo=4)
    resultado = avaliar_casos(casos, cfg)
    assert resultado["imobilidade_prolongada"]["imobilidade_controle"].unidades_normais_com_evento == 0
    assert resultado["saida_do_leito"]["saida_dia_controle"].unidades_normais_com_evento == 0


def test_caso_perdido_aparece_na_lista(cfg):
    serie = _serie_real(6)
    caso = Caso(
        id="x", cenario="queda_dia", event_type="queda", positivo=True,
        inicio=instante("03:00"), fim=instante("03:00"), severidade=Severidade.ALTA,
        observacoes=serie, descricao="sem queda nenhuma",
    )
    r = avaliar_casos([caso], cfg)
    assert r["queda"]["total"].recall == 0
    assert r["queda"]["total"].perdidas == ["x: sem queda nenhuma"]


def test_wilson():
    lo, hi = wilson(14, 16)
    assert 0.6 < lo < 0.875 < hi < 1


def test_particao_mantem_duplicados_juntos():
    assert particao("B_D_0003") in {"calibracao", "teste"}
    assert particao("B_D_0003") == particao("B_D_0003")


def test_ler_rotulos(tmp_path):
    csv = tmp_path / "r.csv"
    csv.write_text("video_id,queda,grupo\nB_D_0003,1,B_D_0003\nB_D_0004,1,B_D_0003\nB_N_96,0,B_N_96\n")
    rotulos = ler_rotulos(csv)
    assert [r.video_id for r in rotulos] == ["B_D_0003", "B_D_0004", "B_N_96"]
    assert rotulos[0].queda and not rotulos[2].queda
    assert rotulos[0].particao == rotulos[1].particao


def _clipe_que_cai():
    caixas = [(round(i / 10, 2), 100.0, 150.0 + (150.0 if i >= 12 else 0.0), 400.0, 250.0 + (150.0 if i >= 12 else 0.0)) for i in range(30)]
    return CaixasDoVideo(caixas, altura=1000, largura=1000, fps=10.0)


def _clipe_parado():
    return CaixasDoVideo([(i / 10, 100.0, 150.0, 400.0, 250.0) for i in range(30)], 1000, 1000, 10.0)


def test_quedas_do_clipe(cfg):
    [a] = quedas_do_clipe(_clipe_que_cai(), cfg)
    assert a.event_type == "queda"
    assert a.severidade is Severidade.ALTA
    assert quedas_do_clipe(_clipe_parado(), cfg) == []


def test_avaliar_clipes():
    rotulos = [
        ClipeRotulado("a", True, "a", "teste"),
        ClipeRotulado("b", True, "b", "teste"),
        ClipeRotulado("c", False, "c", "teste"),
    ]
    quedas = {"a": ["x"], "b": [], "c": ["y"]}
    c = avaliar_clipes(rotulos, quedas)["teste"]
    assert c.recall == 0.5
    assert c.unidades_normais_com_evento == 1
    assert c.perdidas == ["b"]
    assert c.alarmes_falsos == ["c"]
