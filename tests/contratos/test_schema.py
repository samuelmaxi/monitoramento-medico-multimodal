"""JSON Schema exportado: sem drift, e com as mesmas regras do pydantic."""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from contratos import EventoAchado
from contratos.schema import CAMINHO_SCHEMA, gerar_schema, serializar


@pytest.fixture(scope="module")
def validador() -> Draft202012Validator:
    schema = json.loads(CAMINHO_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def test_arquivo_de_schema_esta_em_dia():
    """Se falhar: rode `python -m contratos.schema` e faça commit do arquivo."""
    assert CAMINHO_SCHEMA.read_text(encoding="utf-8") == serializar(gerar_schema())


def test_campos_da_dod_sao_obrigatorios_no_schema(validador):
    obrigatorios = set(validador.schema["required"])
    assert {"patient_id", "modality", "timestamp", "event_type", "score",
            "severity", "evidence", "model_version", "schema_version"} <= obrigatorios


def test_exemplos_passam_no_schema(validador, exemplos):
    assert len(exemplos) >= 7
    for nome, dados in exemplos.items():
        erros = [e.message for e in validador.iter_errors(dados)]
        assert not erros, f"{nome}: {erros}"


def test_saida_do_pydantic_passa_no_schema(validador, evento):
    assert not list(validador.iter_errors(evento.para_dict()))


# Casos inválidos: o JSON Schema e o pydantic precisam concordar.
CASOS_INVALIDOS = {
    "event_type_de_outra_modalidade": {"event_type": "termo_critico"},
    "fusao_sem_relacionados": {"modality": "fusao", "event_type": "risco_multimodal"},
    "sem_achados_com_severidade_alta": {"event_type": "sem_achados"},
    "timestamp_com_offset_local": {"timestamp": "2026-09-26T14:02:00-03:00"},
    "timestamp_sem_fuso": {"timestamp": "2026-09-26T17:02:00"},
    "patient_id_cru": {"patient_id": "10000032"},
    "score_acima_de_1": {"score": 1.5},
    "severidade_invalida": {"severity": "critica"},
    "campo_extra": {"nome": "Fulano"},
    "model_version_sem_arroba": {"model_version": "1.0.0"},
}


@pytest.mark.parametrize("caso", sorted(CASOS_INVALIDOS))
def test_schema_e_pydantic_rejeitam_os_mesmos_casos(validador, campos_validos, caso):
    dados = {**campos_validos, "schema_version": "1.0.0", **CASOS_INVALIDOS[caso]}
    assert list(validador.iter_errors(dados)), f"JSON Schema aceitou '{caso}'"
    with pytest.raises(ValidationError):
        EventoAchado.model_validate(dados)
