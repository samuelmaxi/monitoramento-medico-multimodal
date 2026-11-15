"""US14: avaliação em casos injetados (geração reprodutível, contagem correta, relatório)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from audio.vocal.avaliacao import CLASSE_NORMAL, avaliar
from audio.vocal.injecao import (
    ROTULOS,
    CasoInjetado,
    fala_normal,
    gerar_caso,
    gerar_casos,
    sessao_sintetica,
)
from scripts.avaliar_us14 import META_RECALL, main, wilson

from .conftest import RAIZ

CLASSES = [r for r in ROTULOS if r != CLASSE_NORMAL]


def test_casos_sao_reprodutiveis_pela_semente_e_cobrem_os_quatro_rotulos():
    a, b = gerar_casos(2, semente=7), gerar_casos(2, semente=7)

    assert [c.id for c in a] == [c.id for c in b]
    assert all(np.array_equal(x.sinal, y.sinal) for x, y in zip(a, b, strict=True))
    assert {c.rotulo for c in a} == set(ROTULOS)
    assert len({c.id for c in a}) == len(a) == 8
    assert not np.array_equal(a[0].sinal, gerar_casos(2, semente=8)[0].sinal)


def test_so_a_dificuldade_respiratoria_leva_eventos_respiratorios_injetados():
    for rotulo in ROTULOS:
        caso = gerar_caso(rotulo, semente=3)
        if rotulo == "dificuldade_respiratoria":
            assert 2 <= len(caso.respiratorio) <= 4
            assert all(0.45 <= score <= 0.9 for _, _, score in caso.respiratorio)
        else:
            assert caso.respiratorio == ()


def test_rotulo_desconhecido_e_recusado():
    with pytest.raises(ValueError, match="rótulo desconhecido"):
        gerar_caso("outra_coisa", semente=0)


def test_sessao_sintetica_coloca_o_score_so_no_quadro_e_na_classe_pedidos():
    sessao = sessao_sintetica([(2, "Cough", 0.8), (50, "Gasp", 0.9)], quadros=5)

    scores = sessao(np.zeros(1, np.float32))
    assert scores.shape == (5, 521)
    assert scores[2, 42] == pytest.approx(0.8)
    assert scores.sum() == pytest.approx(0.8)  # o quadro 50 não existe e é ignorado


def test_avaliacao_pequena_atinge_a_meta_de_recall_e_nao_erra_voz_normal(cfg_ingestao, cfg_vocal):
    contagens = avaliar(gerar_casos(3, semente=0), cfg_ingestao, cfg_vocal)

    for rotulo in CLASSES:
        c = contagens[rotulo]
        assert c.casos == 3
        assert c.acertos / c.casos >= META_RECALL, (rotulo, c.perdidos)
    normais = contagens[CLASSE_NORMAL]
    assert normais.acertos == normais.casos == 3  # nenhuma classe disparou em voz normal


def test_a_classe_principal_do_caso_injetado_e_a_pretendida(cfg_ingestao, cfg_vocal):
    contagens = avaliar(gerar_casos(2, semente=0), cfg_ingestao, cfg_vocal)

    for rotulo in ROTULOS:
        assert contagens[rotulo].classe_principal_certa == 2, rotulo


def test_alteracoes_se_sobrepoem_e_o_relatorio_mostra_quais_classes_dispararam(
    cfg_ingestao, cfg_vocal
):
    contagens = avaliar(gerar_casos(3, semente=0), cfg_ingestao, cfg_vocal)

    disartria = contagens["possivel_disartria"].disparos
    assert disartria["possivel_disartria"] == 3
    assert sum(disartria.values()) > 3  # outras classes disparam junto: não são exclusivas
    assert contagens[CLASSE_NORMAL].disparos == {CLASSE_NORMAL: 3}


def test_caso_rotulado_errado_e_contado_como_perdido(cfg_ingestao, cfg_vocal):
    """A contagem precisa conseguir errar: voz normal rotulada como fadiga é um caso perdido."""
    sinal, falas = fala_normal()
    caso = CasoInjetado("fadiga-falsa", "fadiga_vocal", sinal, falas)

    c = avaliar([caso], cfg_ingestao, cfg_vocal)["fadiga_vocal"]

    assert (c.casos, c.acertos) == (1, 0)
    assert c.perdidos == ["fadiga-falsa"]


def test_voz_alterada_rotulada_como_normal_e_falso_positivo(cfg_ingestao, cfg_vocal):
    alterado = gerar_caso("possivel_disartria", semente=1)
    caso = CasoInjetado("normal-falso", CLASSE_NORMAL, alterado.sinal, alterado.falas)

    c = avaliar([caso], cfg_ingestao, cfg_vocal)[CLASSE_NORMAL]

    assert (c.casos, c.acertos) == (1, 0)
    assert "possivel_disartria" in c.disparos


def test_wilson_intervalo_de_confianca():
    assert wilson(0, 0) is None
    inferior, superior = wilson(10, 10)
    assert superior == pytest.approx(1.0, abs=1e-3)
    assert 0.65 < inferior < 0.75
    inferior, superior = wilson(5, 10)
    assert inferior < 0.5 < superior


def test_script_grava_o_relatorio_com_protocolo_e_limitacoes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saida = tmp_path / "avaliacao.json"

    codigo = main([
        "--sementes", "1", "--por-classe", "2", "--saida", str(saida),
        "--config", str(RAIZ / "audio" / "vocal" / "config.yaml"),
        "--config-ingestao", str(RAIZ / "audio" / "ingestao" / "config.yaml"),
    ])

    assert codigo == 0
    relatorio = json.loads(saida.read_text(encoding="utf-8"))
    assert relatorio["model_version"] == "audio-achados@0.1.0"
    assert relatorio["protocolo"]["meta_recall"] == META_RECALL
    assert any("não é diagnóstico" in x for x in relatorio["protocolo"]["limitacoes"])
    assert any("YAMNet" in x for x in relatorio["protocolo"]["limitacoes"])
    assert set(relatorio["por_rotulo"]) == set(ROTULOS)
    assert relatorio["por_rotulo"]["fadiga_vocal"]["casos"] == 2
    assert relatorio["falso_positivo_em_normal"]["casos"] == 2
    assert 0 <= relatorio["recall_medio_das_classes"] <= 1
