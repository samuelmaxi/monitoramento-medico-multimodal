import copy

import pytest
import yaml

from contratos import Severidade
from movimentacao.config import (
    CAMINHO_PADRAO,
    ErroDeConfiguracao,
    carregar_config,
    config_de_dict,
)


@pytest.fixture
def bruto() -> dict:
    with CAMINHO_PADRAO.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_carrega_padrao():
    cfg = carregar_config()
    assert cfg.imobilidade.limiar_min == 120
    assert cfg.saida_leito.severidade_noite is Severidade.ALTA
    assert cfg.queda.severidade is Severidade.ALTA
    assert cfg.versao.startswith("1.")


def test_limiar_configuravel(bruto):
    dados = copy.deepcopy(bruto)
    dados["imobilidade"]["limiar_min"] = 90
    assert config_de_dict(dados).imobilidade.limiar_min == 90


def test_recusa_chave_desconhecida(bruto):
    dados = copy.deepcopy(bruto)
    dados["imobilidade"]["limiar"] = 90
    with pytest.raises(ErroDeConfiguracao, match="limiar"):
        config_de_dict(dados)


def test_recusa_severidade_fora_do_catalogo(bruto):
    dados = copy.deepcopy(bruto)
    dados["saida_leito"]["severidade_noite"] = "critica"
    with pytest.raises(ErroDeConfiguracao, match="severidade_noite"):
        config_de_dict(dados)


def test_recusa_noite_mal_formatada(bruto):
    dados = copy.deepcopy(bruto)
    dados["noite"]["inicio"] = "25:00"
    with pytest.raises(ErroDeConfiguracao, match=r"noite\.inicio"):
        config_de_dict(dados)


def test_recusa_fuso_inexistente(bruto):
    dados = copy.deepcopy(bruto)
    dados["fuso"] = "Marte/Olympus"
    with pytest.raises(ErroDeConfiguracao, match="fuso"):
        config_de_dict(dados)


def test_recusa_fator_de_agitacao_menor_que_1(bruto):
    dados = copy.deepcopy(bruto)
    dados["baseline"]["agitacao_fator"] = 0.8
    with pytest.raises(ErroDeConfiguracao, match="agitacao_fator"):
        config_de_dict(dados)


def test_bloco_video(bruto):
    cfg = config_de_dict(bruto)
    assert cfg.video.area_fora_do_leito == "lateral_leito"
    assert cfg.video.queda_caixa.tempo_max_s > 0


def test_recusa_fracao_de_queda_fora_de_0_1(bruto):
    dados = copy.deepcopy(bruto)
    dados["video"]["queda_caixa"]["queda_altura_frac"] = 1.5
    with pytest.raises(ErroDeConfiguracao, match="queda_altura_frac"):
        config_de_dict(dados)


def test_bloco_queda_descida(bruto):
    cfg = config_de_dict(bruto)
    assert 0 < cfg.video.queda_descida.descida_min_frac < 1
    assert cfg.video.detector.imgsz >= 640
