"""risco_multimodal no contrato US04: explicabilidade, rastreabilidade e cobertura."""

from __future__ import annotations

import pytest

from contratos import EventoAchado, Modalidade, Severidade
from fusao import MODEL_VERSION, RISCO_MULTIMODAL, alinhar_paciente, calcular_risco, evento_de_risco
from fusao.cenarios import cenario_dispneia
from fusao.score import Risco

from .conftest import PACIENTE, T0, achado, instante


@pytest.fixture
def risco_demo(config) -> Risco:
    eventos = cenario_dispneia(PACIENTE, T0)
    ate = max(e.window.end if e.window else e.timestamp for e in eventos)
    return calcular_risco(alinhar_paciente(eventos, PACIENTE, ate, config.janela), config)


def test_evento_valido_no_contrato(risco_demo, config):
    evento = evento_de_risco(risco_demo, config)

    # ida e volta pelo JSON (o contrato serializa em milissegundos)
    assert EventoAchado.de_json(evento.para_json()).para_dict() == evento.para_dict()
    assert evento.modality is Modalidade.FUSAO
    assert evento.event_type == RISCO_MULTIMODAL
    assert evento.model_version == MODEL_VERSION
    assert evento.score == pytest.approx(risco_demo.score, abs=1e-4)
    assert evento.severity is Severidade.ALTA
    assert evento.timestamp == risco_demo.ate
    assert evento.window.start == risco_demo.ate - config.janela
    assert evento.is_synthetic  # os achados do cenário são sintéticos


def test_features_explicam_o_score(risco_demo, config):
    features = evento_de_risco(risco_demo, config).evidence.features
    contribs = [v for k, v in features.items() if k.startswith("contrib_")]

    assert len(contribs) == 7  # 6 modalidades + concordância
    assert sum(contribs) == pytest.approx(risco_demo.score, abs=1e-3)
    assert features["contrib_video"] == 0 and features["forca_video"] == 0
    assert features["nivel"] == "alto"
    assert features["eixo_concordancia"] == "respiratorio"
    assert features["modalidades_ausentes"] == "video,movimentacao"
    assert features["modalidades_com_achado"] == 3
    assert features["ganho_fusao"] > 0


def test_resumo_legivel(risco_demo, config):
    resumo = evento_de_risco(risco_demo, config).evidence.summary
    assert resumo.startswith("Risco alto (0,")
    assert "sinais vitais" in resumo and "áudio" in resumo and "texto" in resumo
    assert "concordância no eixo respiratório" in resumo
    assert "sem dados de vídeo, movimentação" in resumo


def test_limiares_e_rastreabilidade(risco_demo, config):
    evento = evento_de_risco(risco_demo, config)
    assert evento.evidence.thresholds["versao_config"] == config.versao
    usados = {e.event_id for e in risco_demo.eventos}
    assert set(evento.related_event_ids) == usados
    contribuintes = {s.evento.event_id for s in risco_demo.sinais.values() if s.evento}
    assert set(evento.related_event_ids[: len(contribuintes)]) == contribuintes


def test_contexto_e_ingestao_do_achado_mais_recente(config):
    eventos = [
        achado(Modalidade.SINAIS_VITAIS, "news2_resposta_urgente", minuto=-10, leito="UTI-01"),
        achado(Modalidade.AUDIO, "fadiga_vocal", minuto=0, leito="UTI-02"),
        achado(Modalidade.TEXTO, "sem_achados", minuto=-5, ingestao=False, leito=None),
    ]
    risco = calcular_risco(alinhar_paciente(eventos, PACIENTE, instante(0), config.janela), config)
    evento = evento_de_risco(risco, config)
    assert evento.context.bed_id == "UTI-02"
    assert evento.context.source_id == "fusao"
    assert evento.ingested_at == instante(0)
    assert not evento.is_synthetic


def test_sem_achados_alinhados_nao_emite(config):
    vazio = calcular_risco(alinhar_paciente([], PACIENTE, instante(0), config.janela), config)
    with pytest.raises(ValueError, match="evento vazio"):
        evento_de_risco(vazio, config)
