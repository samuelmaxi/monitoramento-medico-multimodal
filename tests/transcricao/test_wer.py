import pytest

from app.wer import calcular_wer, classificar_wer, normalizar, wer_agregado


def test_textos_iguais_dao_wer_zero():
    assert calcular_wer("bom dia doutor", "bom dia doutor").wer == 0.0


def test_normalizacao_ignora_caixa_e_pontuacao():
    assert normalizar("Dor, no PEITO!") == ["dor", "no", "peito"]
    assert calcular_wer("Dor, no peito!", "dor no peito").wer == 0.0


def test_substituicao_conta_um_erro_em_quatro_palavras():
    resultado = calcular_wer("tomo a dipirona hoje", "tomo a dipirone hoje")

    assert resultado.wer == pytest.approx(0.25)
    assert (resultado.substituicoes, resultado.delecoes, resultado.insercoes) == (1, 0, 0)


def test_delecao_conta_palavra_que_sumiu():
    resultado = calcular_wer("sinto falta de ar forte", "sinto falta ar forte")

    assert (resultado.substituicoes, resultado.delecoes, resultado.insercoes) == (0, 1, 0)
    assert resultado.wer == pytest.approx(1 / 5)


def test_insercao_conta_palavra_extra():
    resultado = calcular_wer("sinto falta de ar", "sinto muita falta de ar")

    assert (resultado.substituicoes, resultado.delecoes, resultado.insercoes) == (0, 0, 1)
    assert resultado.wer == pytest.approx(1 / 4)


def test_referencia_vazia():
    assert calcular_wer("", "").wer == 0.0
    assert calcular_wer("", "algo").wer == 1.0


def test_wer_agregado_pondera_pelas_palavras_de_referencia():
    primeiro = calcular_wer("um dois tres quatro", "um dois tres cinco")  # 1 de 4
    segundo = calcular_wer("a b c d e f", "a b x y z f")  # 3 de 6

    assert wer_agregado([primeiro, segundo]) == pytest.approx(4 / 10)


@pytest.mark.parametrize(
    ("wer", "trecho"),
    [
        (0.05, "boa qualidade"),
        (0.15, "aceitável"),
        (0.20, "aceitável"),
        (0.25, "acima do limite"),
        (0.30, "ruim"),
    ],
)
def test_classificacao_segue_faixas_do_dod(wer, trecho):
    assert trecho in classificar_wer(wer)


def test_sem_interjeicoes_remove_hmm_dos_dois_lados():
    bruto = calcular_wer("hmm, dor no peito", "dor no peito")
    limpo = calcular_wer("hmm, dor no peito", "dor no peito", remover_interjeicoes=True)

    assert bruto.wer == pytest.approx(1 / 4)
    assert limpo.wer == 0.0
