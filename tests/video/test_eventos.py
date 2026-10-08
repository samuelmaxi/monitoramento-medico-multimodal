"""Tradução das transições para o contrato de eventos da US04."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from jsonschema import Draft202012Validator

from contratos import Modalidade
from contratos.schema import gerar_schema
from video import Caixa, ResumoExecucao, TradutorEventos, Transicao
from video.areas import AvaliacaoContencao
from video.monitor import EventoAreaCritica

from .conftest import deteccao


@pytest.mark.parametrize(
    ("tipo_transicao", "tipo_evento"),
    [
        (Transicao.ENTRADA, "entrada_area_critica"),
        (Transicao.SAIDA, "saida_area_critica"),
    ],
)
def test_evento_usa_contrato_us04_e_schema(config_base, emissor, tipo_transicao, tipo_evento):
    deteccao_atual = deteccao(
        Caixa(10, 10, 90, 90), frame_index=15, tempo_s=1.5, track_id=42, confianca=0.87
    )
    transicao = EventoAreaCritica(
        transicao=tipo_transicao,
        deteccao=deteccao_atual,
        id_area="teste",
        nome_area="Área de teste",
        contencao=AvaliacaoContencao(True, 0.75, "fracao"),
        track_id=42,
        dentro_ha_quadros=3,
    )
    tradutor = TradutorEventos(config_base)
    contexto = tradutor.contexto_de(descricao_modelo="yolov8n@8.4.173")
    referencia = datetime(2026, 10, 5, 19, 0, tzinfo=UTC)
    evento = tradutor.emitir(transicao, emissor, contexto=contexto, detectado_em=referencia)

    assert evento.event_type == tipo_evento
    assert evento.modality is Modalidade.VIDEO
    assert evento.timestamp == datetime(2026, 10, 5, 19, 0, 1, 500000, tzinfo=UTC)
    assert evento.score == pytest.approx(0.87)
    assert evento.evidence.features["classe"] == "person"
    assert evento.evidence.features["area_critica_id"] == "teste"
    assert evento.evidence.features["transicao"] == tipo_transicao.name.lower()
    assert evento.evidence.features["frame_index"] == 15
    assert evento.evidence.features["track_id"] == 42
    Draft202012Validator(gerar_schema()).validate(evento.para_dict())
    assert emissor.eventos == [evento]


def test_inicio_utc_e_deslocado_pelo_tempo_video(config_base):
    tradutor = TradutorEventos(config_base)
    timestamp = tradutor.instante_do_quadro(12.25)
    assert timestamp == datetime(2026, 10, 5, 19, 0, 12, 250000, tzinfo=UTC)


def test_inicio_utc_com_fuso_nao_utc_e_rejeitado(config_base):
    from dataclasses import replace

    config = replace(config_base, fonte=replace(config_base.fonte, inicio_utc="2026-10-05T16:00:00-03:00"))
    with pytest.raises(ValueError, match="UTC"):
        TradutorEventos(config).instante_do_quadro(1.0)


def resumo_sem_transicoes():
    return ResumoExecucao(
        quadros_lidos=120,
        deteccoes_total=8,
        transicoes=0,
        eventos=0,
        entradas=0,
        saidas=0,
        fps=10.0,
        resolucao=(320, 240),
        classes_detectadas={"person": 8},
        duracao_s=12.4,
    )


def test_sem_achados_documenta_processo_sem_transicao(config_base, emissor):
    tradutor = TradutorEventos(config_base)
    contexto = tradutor.contexto_de(descricao_modelo="yolov8n@8.4.173")
    referencia = datetime(2026, 10, 5, 19, 0, tzinfo=UTC)
    evento = tradutor.emitir_sem_achados(
        emissor, resumo=resumo_sem_transicoes(), contexto=contexto, detectado_em=referencia
    )

    assert evento.event_type == "sem_achados"
    assert evento.modality is Modalidade.VIDEO
    assert evento.severity.value == "info"
    assert evento.score == 0.0
    assert evento.timestamp == datetime(2026, 10, 5, 19, 0, tzinfo=UTC)
    assert evento.window is not None
    assert evento.window.start == evento.timestamp
    assert evento.window.end == datetime(2026, 10, 5, 19, 0, 11, 900000, tzinfo=UTC)
    assert evento.context.source_id == "cam-test"
    assert evento.evidence.features["quadros_lidos"] == 120
    assert evento.evidence.features["deteccoes_total"] == 8
    assert evento.evidence.features["entradas"] == 0
    assert evento.evidence.features["saidas"] == 0
    assert evento.evidence.features["resolucao"] == "320x240"
    assert evento.evidence.features["classes_detectadas"] == "person: 8"
    assert "sem quedas ou saídas" in evento.evidence.summary
    Draft202012Validator(gerar_schema()).validate(evento.para_dict())
    assert emissor.eventos == [evento]


def test_sem_achados_nao_e_emitido_quando_ha_transicoes(config_base, emissor):
    tradutor = TradutorEventos(config_base)
    contexto = tradutor.contexto_de(descricao_modelo="yolov8n@8.4.173")
    tradutor.emitir_sem_achados(emissor, resumo=resumo_sem_transicoes(), contexto=contexto)
    assert len(emissor.eventos) == 1
