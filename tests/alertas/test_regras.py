from __future__ import annotations

from datetime import timedelta

import pytest

from alertas import carregar_regras
from alertas.regras import avaliar, regras_de_dict
from contratos import Severidade
from contratos.catalogo import Modalidade

from .conftest import RAIZ
from .fabrica_eventos import evento

POLITICAS = {
    "alta": {"canais": ["email", "webhook"], "janela_dedup_min": 5,
             "escalar_apos_ocorrencias": 10, "escalar_apos_min": 60, "acao_padrao": "Avaliar agora."},
    "media": {"canais": ["email"], "janela_dedup_min": 15,
              "escalar_apos_ocorrencias": 5, "escalar_apos_min": 30, "acao_padrao": "Revisar."},
    "baixa": {"canais": [], "janela_dedup_min": 60,
              "escalar_apos_ocorrencias": 10, "escalar_apos_min": 120, "acao_padrao": "Registrar."},
}


def _dados(**extra) -> dict:
    return {"politicas": POLITICAS, "regras": [], **extra}


def test_yaml_do_repositorio_carrega_e_tem_politicas_das_tres_prioridades():
    regras = carregar_regras(RAIZ / "config" / "alertas_regras.yaml")

    assert regras.politica(Severidade.ALTA).canais == ("email", "webhook")
    assert regras.politica(Severidade.BAIXA).canais == ()
    assert regras.politica(Severidade.BAIXA).janela_dedup == timedelta(minutes=60)


def test_regra_especifica_define_prioridade_e_acao(regras):
    decisao = avaliar(evento(Modalidade.SINAIS_VITAIS, "news2_emergencia", Severidade.MEDIA), regras)

    assert decisao.prioridade is Severidade.ALTA
    assert decisao.origem == "regra:sinais_vitais/news2_emergencia"
    assert decisao.acao_sugerida.startswith("Avaliação de emergência")
    assert decisao.canais == ("email", "webhook")


def test_sem_regra_vale_a_severidade_do_proprio_achado(regras):
    decisao = avaliar(evento(Modalidade.VIDEO, "entrada_area_critica", Severidade.MEDIA), regras)

    assert decisao.prioridade is Severidade.MEDIA
    assert decisao.origem == "padrao:media"
    assert decisao.acao_sugerida == "Revisar o achado no painel e reavaliar o paciente ainda neste turno."


def test_info_nunca_vira_decisao(regras):
    assert avaliar(evento(Modalidade.VIDEO, "postura_fora_da_faixa", Severidade.INFO), regras) is None


def test_sem_achados_nunca_vira_decisao(regras):
    assert avaliar(evento(Modalidade.VIDEO, "sem_achados", Severidade.INFO), regras) is None


def test_regra_curinga_vale_para_a_modalidade_inteira():
    regras = regras_de_dict(_dados(regras=[
        {"modalidade": "texto", "tipo": "*", "prioridade": "alta", "acao_sugerida": "Ligar para a equipe."},
    ]))

    decisao = avaliar(evento(Modalidade.TEXTO, "sentimento_negativo", Severidade.BAIXA), regras)

    assert decisao.prioridade is Severidade.ALTA
    assert decisao.origem == "regra:texto/*"


def test_regra_especifica_prevalece_sobre_curinga():
    regras = regras_de_dict(_dados(regras=[
        {"modalidade": "texto", "tipo": "*", "prioridade": "alta"},
        {"modalidade": "texto", "tipo": "termo_critico", "prioridade": "media"},
    ]))

    decisao = avaliar(evento(Modalidade.TEXTO, "termo_critico", Severidade.ALTA), regras)

    assert decisao.prioridade is Severidade.MEDIA


@pytest.mark.parametrize(
    ("regra", "mensagem"),
    [
        ({"modalidade": "sinais_vitais", "tipo": "queda", "prioridade": "alta"}, "não existe na modalidade"),
        ({"modalidade": "sinais_vitais", "tipo": "sem_achados", "prioridade": "alta"}, "não existe na modalidade"),
        ({"modalidade": "marte", "tipo": "*", "prioridade": "alta"}, "modalidade desconhecida"),
        ({"modalidade": "texto", "tipo": "*", "prioridade": "info"}, "não gera alerta"),
        ({"modalidade": "texto", "tipo": "*", "prioridade": "critica"}, "prioridade desconhecida"),
    ],
)
def test_regra_invalida_falha_no_carregamento(regra, mensagem):
    with pytest.raises(ValueError, match=mensagem):
        regras_de_dict(_dados(regras=[regra]))


def test_regra_duplicada_falha():
    regra = {"modalidade": "texto", "tipo": "termo_critico", "prioridade": "alta"}
    with pytest.raises(ValueError, match="duplicada"):
        regras_de_dict(_dados(regras=[regra, regra]))


def test_politica_faltando_falha():
    politicas = {k: v for k, v in POLITICAS.items() if k != "baixa"}
    with pytest.raises(ValueError, match="baixa"):
        regras_de_dict({"politicas": politicas, "regras": []})
