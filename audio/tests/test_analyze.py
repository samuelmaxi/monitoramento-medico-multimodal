"""Testes da US13 com a API mockada: filtro de asserção, escopo do paciente e eventos no contrato."""

from __future__ import annotations

import json
import logging

import pytest
from jsonschema import Draft202012Validator

from audio.text.analyze import analyze_transcript
from audio.text.config import load_critical_terms
from contratos import BarramentoLocal, EmissorMemoria, Severidade
from contratos.schema import CAMINHO_SCHEMA
from contratos.testing import assert_eventos_validos

from .conftest import CONFIG_PATH, consulta


def _termos(resultado) -> dict[str, object]:
    return {e.evidence.features["termo"]: e for e in resultado.events if e.event_type == "termo_critico"}


def test_lista_de_termos_criticos_tem_de_10_a_15_termos_com_sinonimos():
    termos = load_critical_terms(CONFIG_PATH.parent / "critical_terms.yaml")
    assert 10 <= len(termos.terms) <= 15
    assert all(t.synonyms and t.categories for t in termos.terms)
    nomes = {t.name for t in termos.terms}
    assert {"dor torácica", "falta de ar", "síncope", "confusão mental", "sangramento", "convulsão"} <= nomes


def test_termo_afirmado_vira_evento_com_severidade_da_lista(config, fake_factory):
    fake, cliente = fake_factory({"dor no peito": {"normalized": "Chest Pain", "certainty": "positive"}})
    t = consulta(("paciente", "Estou com dor no peito desde ontem."))

    resultado = analyze_transcript(t, config, cliente)

    evento = _termos(resultado)["dor torácica"]
    assert evento.severity is Severidade.ALTA
    assert evento.score == pytest.approx(0.95)
    assert evento.evidence.features["certeza"] == "positive"
    assert evento.evidence.features["rebaixada"] is False
    assert fake.health_calls[0]["language"] == "pt"
    assert fake.sentiment_calls[0]["language"] == "pt-BR"


def test_negacao_e_descartada_e_registrada_no_log(config, fake_factory, caplog):
    _, cliente = fake_factory({"dor no peito": {"normalized": "Chest Pain", "certainty": "negative"}})
    t = consulta(("medico", "Alguma dor no peito?"), ("paciente", "Nego dor no peito."))

    with caplog.at_level(logging.INFO, logger="audio.text.analyze"):
        resultado = analyze_transcript(t, config, cliente)

    assert _termos(resultado) == {}
    assert [e.event_type for e in resultado.events] == ["sem_achados"]
    descarte, = resultado.details["descartes"]
    assert descarte["motivo"] == "certeza=negative"
    assert descarte["termo_critico"] == "dor torácica"
    assert "certeza=negative" in caplog.text


def test_negacao_possivel_tambem_e_descartada(config, fake_factory):
    _, cliente = fake_factory({"falta de ar": {"certainty": "negativePossible"}})
    t = consulta(("paciente", "Acho que não é falta de ar, é mais um cansaço."))

    resultado = analyze_transcript(t, config, cliente)

    assert _termos(resultado) == {}
    assert resultado.details["descartes"][0]["motivo"] == "certeza=negative_possible"


def test_sintoma_de_terceiro_e_descartado(config, fake_factory, caplog):
    _, cliente = fake_factory({"infarto": {"category": "Diagnosis", "association": "other"}})
    t = consulta(("paciente", "Meu pai teve infarto com cinquenta anos."))

    with caplog.at_level(logging.INFO, logger="audio.text.analyze"):
        resultado = analyze_transcript(t, config, cliente)

    assert _termos(resultado) == {}
    assert resultado.details["descartes"][0]["motivo"] == "associacao=other"
    assert "associacao=other" in caplog.text


@pytest.mark.parametrize("condicionalidade", ["hypothetical", "conditional"])
def test_hipotetico_e_condicional_sao_descartados(config, fake_factory, condicionalidade):
    _, cliente = fake_factory({"falta de ar": {"conditionality": condicionalidade}})
    t = consulta(("paciente", "Se a falta de ar piorar, eu devo voltar?"))

    resultado = analyze_transcript(t, config, cliente)

    assert _termos(resultado) == {}
    assert resultado.details["descartes"][0]["motivo"] == f"condicionalidade={condicionalidade}"


def test_passado_do_servico_mantem_termo_com_severidade_rebaixada(config, fake_factory):
    _, cliente = fake_factory({"convulsão": {"temporality": "past"}})
    t = consulta(("paciente", "Eu tive uma convulsão, mas nunca mais aconteceu."))

    evento = _termos(analyze_transcript(t, config, cliente))["convulsão"]

    assert evento.severity is Severidade.MEDIA  # alta → media
    assert evento.evidence.features["severidade_lista"] == "alta"
    assert evento.evidence.features["rebaixada"] is True
    assert evento.evidence.features["temporalidade_fonte"] == "servico"


def test_passado_pela_regra_local_quando_o_servico_nao_devolve_temporalidade(config, fake_factory):
    _, cliente = fake_factory({"infarto": {"category": "Diagnosis", "certainty": "positive"}})
    t = consulta(("paciente", "Tive um infarto há dois anos e coloquei um stent."))

    evento = _termos(analyze_transcript(t, config, cliente))["infarto"]

    assert evento.severity is Severidade.MEDIA
    assert evento.evidence.features["temporalidade"] == "past"
    assert evento.evidence.features["temporalidade_fonte"] == "regra_local"


def test_fala_do_medico_nao_vai_para_a_azure_nem_gera_alerta(config, fake_factory):
    fake, cliente = fake_factory({"falta de ar": {}})
    t = consulta(("medico", "Você sente falta de ar?"), ("paciente", "Não sei dizer, doutor."))

    resultado = analyze_transcript(t, config, cliente)

    enviados = [d["text"] for chamada in fake.health_calls for d in chamada["documents"]]
    assert enviados == ["Não sei dizer, doutor."]
    assert _termos(resultado) == {}


def test_pergunta_do_medico_fica_como_contexto_da_evidencia(config, fake_factory):
    _, cliente = fake_factory({"falta de ar": {}})
    t = consulta(("medico", "Você sente falta de ar?"), ("paciente", "Sinto falta de ar até para falar."))

    evento = _termos(analyze_transcript(t, config, cliente))["falta de ar"]

    assert evento.evidence.features["pergunta_medico"] == "Você sente falta de ar?"
    assert evento.evidence.features["falante"] == "paciente"


def test_termo_repetido_gera_um_evento_com_contagem(config, fake_factory):
    _, cliente = fake_factory({"febre": {}})
    t = consulta(("paciente", "Estou com febre."), ("medico", "Desde quando?"), ("paciente", "A febre começou ontem."))

    eventos = [e for e in analyze_transcript(t, config, cliente).events if e.event_type == "termo_critico"]

    assert len(eventos) == 1
    assert eventos[0].evidence.features["ocorrencias"] == 2


def test_consulta_predominantemente_negativa_gera_evento_de_sentimento(config, fake_factory):
    _, cliente = fake_factory()
    t = consulta(("paciente", "Ando muito triste. Me sinto mal o tempo todo. Hoje vim de ônibus."))

    resultado = analyze_transcript(t, config, cliente)

    resumo = resultado.details["sentimento"]["resumo"]
    assert resumo["total_sentencas"] == 3
    assert resumo["sentencas_negativas"] == 2
    evento, = [e for e in resultado.events if e.event_type == "sentimento_negativo"]
    assert evento.severity is Severidade.BAIXA
    assert evento.evidence.thresholds["limiar_negativo"] == 0.7
    assert evento.evidence.features["pct_negativas"] == pytest.approx(2 / 3, abs=1e-4)


def test_eventos_seguem_o_contrato_pydantic_e_o_json_schema(config, fake_factory, tmp_path):
    regras = {
        "dor no peito": {"normalized": "Chest Pain", "certainty": "positive"},
        "falta de ar": {"certainty": "negative"},
        "convulsão": {"temporality": "past"},
    }
    _, cliente = fake_factory(regras)
    emissor = EmissorMemoria()
    t = consulta(
        ("medico", "O que o senhor está sentindo?"),
        ("paciente", "Estou com dor no peito e muito medo. Nego falta de ar. Tive uma convulsão quando era criança."),
    )

    resultado = analyze_transcript(t, config, cliente, emissor)

    assert emissor.eventos == resultado.events
    saida = tmp_path / "consulta-teste.json"
    saida.write_text(json.dumps([e.para_dict() for e in resultado.events], ensure_ascii=False), encoding="utf-8")
    eventos = assert_eventos_validos(saida, modalidade="texto", minimo=2)

    validador = Draft202012Validator(json.loads(CAMINHO_SCHEMA.read_text(encoding="utf-8")),
                                     format_checker=Draft202012Validator.FORMAT_CHECKER)
    for evento in eventos:
        erros = list(validador.iter_errors(evento.para_dict()))
        assert not erros, [e.message for e in erros]
        assert evento.model_version == config.model_version
        assert evento.patient_id.startswith("pt_")
        assert evento.evidence.models["azure-text-analytics-for-health"] == "2023-04-28"
        assert evento.evidence.models["azure-language-sentiment"] == "2025-01-01"
    assert {e.event_type for e in eventos} == {"termo_critico"}  # só 1 de 3 sentenças negativa


def test_publica_os_eventos_no_barramento(config, fake_factory, tmp_path):
    _, cliente = fake_factory({"dor no peito": {}, "febre": {}})
    barramento = BarramentoLocal(tmp_path / "eventos.jsonl")
    t = consulta(("paciente", "Estou com dor no peito e febre."))

    analyze_transcript(t, config, cliente, barramento)

    assert not barramento.falhas
    assert barramento.total_publicados == 2
