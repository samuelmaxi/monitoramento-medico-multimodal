from datetime import timedelta

import pytest

from contratos import Modalidade, Severidade
from contratos.testing import assert_eventos_validos
from movimentacao import MODEL_VERSION
from movimentacao.eventos import montar_evento, montar_sem_achados, relatorio
from movimentacao.modelos import Observacao
from movimentacao.modelos import Posicao as P
from movimentacao.regras import detectar

from .conftest import instante, obs


def _imobilidade_160min(cfg):
    serie = [Observacao(instante("14:15") + timedelta(minutes=20 * i), P.SUPINO, "mimic_position") for i in range(8)]
    serie.append(obs("16:55", P.LATERAL_ESQ, fonte="mimic_position"))
    [a] = detectar(serie, cfg)
    return serie, a


def test_formato_igual_ao_exemplo_do_contrato(cfg, chave):
    _, a = _imobilidade_160min(cfg)
    e = montar_evento(a, cfg, paciente="10005817", internacao="20626031", bed_id="UTI-07")
    assert_eventos_validos([e])
    assert e.modality is Modalidade.MOVIMENTACAO
    assert e.event_type == "imobilidade_prolongada"
    assert e.evidence.features["minutos_sem_mudanca"] == 160
    assert e.evidence.thresholds["limiar_min"] == 120
    assert e.evidence.thresholds["versao_limiares"] == cfg.versao
    assert e.evidence.summary == "2 h 40 min sem mudança de posição no leito"
    assert e.timestamp == instante("16:55")
    assert (e.window.start, e.window.end) == (instante("14:15"), instante("16:55"))
    assert e.severity is Severidade.MEDIA
    assert e.model_version == MODEL_VERSION == "anomalias-movimentacao@0.1.0"
    assert e.context.bed_id == "UTI-07"


def test_ids_pseudonimizados(cfg, chave):
    _, a = _imobilidade_160min(cfg)
    e = montar_evento(a, cfg, paciente="10005817", internacao="20626031")
    assert e.patient_id.startswith("pt_")
    assert "10005817" not in e.para_json()
    assert e.context.encounter_id.startswith("enc_")


def test_pseudonimo_passa_como_esta(cfg):
    _, a = _imobilidade_160min(cfg)
    e = montar_evento(a, cfg, paciente="pt_" + "a" * 24)
    assert e.patient_id == "pt_" + "a" * 24


def test_todos_os_tipos_viram_eventos_validos(cfg, chave):
    serie = [
        obs("22:30", P.SUPINO), obs("23:10", P.FORA_DO_LEITO), obs("23:20", P.SUPINO),
        obs("23:40", P.CHAO, fonte="video_caixa", queda=True),
    ]
    eventos = [montar_evento(a, cfg, paciente="1", sintetico=True) for a in detectar(serie, cfg)]
    assert {e.event_type for e in eventos} == {"saida_do_leito", "queda"}
    assert all(e.is_synthetic for e in eventos)
    assert_eventos_validos(eventos)


def test_sem_achados(cfg, chave):
    serie = [obs("08:00", P.SUPINO), obs("09:00", P.LATERAL_ESQ)]
    e = montar_sem_achados(serie, cfg, paciente="1")
    assert_eventos_validos([e])
    assert e.event_type == "sem_achados"
    assert e.severity is Severidade.INFO
    assert e.evidence.features["reposicionamentos_por_hora"] == 1.0


def test_sem_achados_sem_observacoes_recusa(cfg):
    with pytest.raises(ValueError, match="observação"):
        montar_sem_achados([], cfg, paciente="pt_" + "a" * 24)


def test_relatorio(cfg):
    serie, a = _imobilidade_160min(cfg)
    r = relatorio(serie, [a], cfg)
    assert r["eventos"] == {"imobilidade_prolongada": 1}
    assert r["observacoes"] == 9
    assert r["fontes"] == {"mimic_position": 9}
    assert r["metricas"]["maior_minutos_sem_mudanca"] == 160
    assert r["versao_limiares"] == cfg.versao
