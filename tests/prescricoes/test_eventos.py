import json
from pathlib import Path

from contratos import SEM_ACHADOS, EventoAchado, Modalidade, e_pseudonimo_de_paciente
from prescricoes.eventos import montar_evento, montar_sem_achados
from prescricoes.mimic import carregar_internacoes, carregar_prescricoes
from prescricoes.regras import detectar, mudancas_de_dose
from prescricoes.run import eventos_de, executar

from .conftest import CHAVE_TESTE, T0, ordem


def test_evento_segue_o_contrato(cfg, internacao):
    a = ordem("Metoprolol Tartrate", "12.5", "mg", de=0, ate=24, freq="BID", n24=2)
    b = ordem("Metoprolol Tartrate", "50", "mg", de=24, ate=72, freq="BID", n24=2)
    [anomalia] = mudancas_de_dose([a, b], cfg, internacao)
    evento = montar_evento(anomalia, cfg, chave=CHAVE_TESTE)
    assert evento.modality is Modalidade.PRESCRICOES
    assert evento.event_type == "mudanca_abrupta_dose"
    assert e_pseudonimo_de_paciente(evento.patient_id)
    assert evento.context.encounter_id.startswith("enc_")
    assert evento.timestamp == b.inicio
    features = evento.evidence.features
    assert features["antes_dose_diaria"] == 25 and features["depois_dose_diaria"] == 100
    assert features["variacao_pct"] == 300
    assert evento.evidence.thresholds["versao_limiares"] == cfg.versao
    assert evento.evidence.thresholds["variacao_min_pct"] == 50
    assert "10000001" not in evento.para_json()  # nenhum ID cru do MIMIC
    assert EventoAchado.de_json(evento.para_json()).para_json() == evento.para_json()


def test_sem_achados_por_internacao_limpa(cfg, internacao):
    comum = ordem("Pantoprazole", "40", "mg", de=1, ate=48)
    anomalias, eventos = eventos_de([comum], {internacao.id: internacao}, cfg, chave=CHAVE_TESTE)
    assert anomalias == []
    [evento] = eventos
    assert evento.event_type == SEM_ACHADOS and evento.timestamp == T0
    assert montar_sem_achados(internacao, 1, cfg, chave=CHAVE_TESTE).severity.value == "info"


def test_ordem_sintetica_marca_evento(cfg, internacao):
    from dataclasses import replace

    a = ordem("Furosemide", "20", "mg", de=0, ate=24)
    b = replace(ordem("Furosemide", "80", "mg", de=24, ate=48), sintetica=True)
    _, eventos = eventos_de([a, b], {internacao.id: internacao}, cfg, chave=CHAVE_TESTE)
    assert [e.is_synthetic for e in eventos if e.event_type != SEM_ACHADOS] == [True]


# --------------------------------------------------------------------------- #
# Ponta a ponta em tabelas no formato do MIMIC
# --------------------------------------------------------------------------- #


def test_carga_ordena_deduplica_e_junta_frequencia(mimic):
    prescricoes = carregar_prescricoes(mimic)
    assert [p.ordem_id for p in prescricoes] == ["101", "103", "102", "201"]  # linha duplicada sai
    assert prescricoes[0].frequencia == "BID" and prescricoes[0].dose_diaria == 25
    assert prescricoes[0].status.startswith("Inactive")
    assert carregar_internacoes(mimic)["11"].alta.year == 2150


def test_executar_grava_eventos_e_relatorio(mimic, tmp_path, cfg):
    resultado = executar(dados=mimic, saida=tmp_path / "saida")
    tipos = sorted(e.event_type for e in resultado.eventos)
    # Paciente 1: heparina (ISMP), metoprolol 25 → 100 mg/dia e metoprolol parado no
    # dia 5 com alta no dia 10. Paciente 2: nada.
    assert tipos == [
        "medicamento_alta_vigilancia", "mudanca_abrupta_dose", SEM_ACHADOS, "suspensao_abrupta",
    ]
    linhas = resultado.caminho_eventos.read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 4 and all(EventoAchado.de_json(l) for l in linhas)
    rel = json.loads(resultado.caminho_relatorio.read_text(encoding="utf-8"))
    assert rel["total_anomalias"] == 3 and rel["internacoes_com_anomalia"] == 1
    assert any("subiu 300%" in a["justificativa"] for a in rel["anomalias"])
    assert len(detectar(carregar_prescricoes(mimic), cfg, carregar_internacoes(mimic))) == 3
