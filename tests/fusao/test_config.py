"""Carga e validação do config/us18_fusao.yaml."""

from __future__ import annotations

import copy
from datetime import timedelta

import pytest
import yaml

from contratos import Modalidade, Severidade
from fusao import ErroDeConfiguracao, de_dict

from .conftest import RAIZ


@pytest.fixture
def bruto():
    return yaml.safe_load((RAIZ / "config" / "us18_fusao.yaml").read_text(encoding="utf-8"))


def test_yaml_do_repositorio_carrega(config):
    assert config.janela == timedelta(minutes=60)
    assert config.pesos[Modalidade.SINAIS_VITAIS] == 1.0
    assert set(config.pesos) == set(Modalidade) - {Modalidade.FUSAO}
    assert config.valor_severidade[Severidade.INFO] == 0
    assert {"respiratorio", "neurologico", "queda"} <= set(config.eixos)
    assert config.nivel_medio < config.nivel_alto


def test_limiares_vao_no_evento_com_a_versao(config):
    limiares = config.limiares()
    assert limiares["versao_config"] == config.versao
    assert limiares["janela_min"] == 60
    assert limiares["peso_sinais_vitais"] == 1.0


def test_com_janela(config):
    assert config.com_janela(15).janela == timedelta(minutes=15)
    assert config.janela == timedelta(minutes=60)  # original intacto
    with pytest.raises(ErroDeConfiguracao):
        config.com_janela(0)


def test_meia_vida_zero_desliga_decaimento(bruto):
    bruto["meia_vida_min"] = 0
    assert de_dict(bruto).meia_vida is None


def _alterar(bruto, caminho, valor):
    dados = copy.deepcopy(bruto)
    alvo = dados
    for chave in caminho[:-1]:
        alvo = alvo[chave]
    if valor is KeyError:
        del alvo[caminho[-1]]
    else:
        alvo[caminho[-1]] = valor
    return dados


@pytest.mark.parametrize(
    ("caminho", "valor", "mensagem"),
    [
        (("chave_nova",), 1, "desconhecida"),
        (("janela_min",), 0, "janela_min"),
        (("janela_min",), KeyError, "janela_min"),
        (("pesos", "video"), 1.5, "pesos.video"),
        (("pesos", "fusao"), 0.5, "desconhecida"),
        (("pesos", "audio"), KeyError, "pesos.audio"),
        (("valor_severidade", "alta"), "muito", "valor_severidade.alta"),
        (("pesos_por_tipo",), {"video/inexistente": 0.5}, "inexistente"),
        (("pesos_por_tipo",), {"fusao/risco_multimodal": 0.5}, "fusão"),
        (("niveis", "medio"), 0.8, "menor"),
        (("concordancia", "fator"), -0.1, "concordancia.fator"),
        (("concordancia", "eixos", "x"), [{"modalidade": "audio", "tipos": ["fadiga_vocal"]}], "2 modalidades"),
        (("concordancia", "eixos", "x"), [{"modalidade": "audio", "tipos": ["queda"]},
                                          {"modalidade": "texto", "tipos": ["termo_critico"]}], "queda"),
        (("emissao", "alertar_com_uma_modalidade"), "sim", "true ou false"),
    ],
)
def test_config_invalida_aponta_a_chave(bruto, caminho, valor, mensagem):
    with pytest.raises(ErroDeConfiguracao, match=mensagem):
        de_dict(_alterar(bruto, caminho, valor))
