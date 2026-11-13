"""Etapas de prescrições (US16) e movimentação (US17) do pipeline, com as regras reais."""

import json

import pytest

from pipeline import ConfigPipeline, OrquestradorPipeline


@pytest.fixture(autouse=True)
def _chave(monkeypatch):
    monkeypatch.setenv("PSEUDONYM_KEY", "chave-de-teste-com-mais-de-32-bytes-000000")


@pytest.fixture
def execucao(tmp_path):
    cfg = ConfigPipeline.from_env(fator_velocidade=0.0, saida_dir=tmp_path, sinais_fonte="sintetico")
    resumo = OrquestradorPipeline(cfg).executar()
    relatorio = json.loads(resumo.caminho_relatorio.read_text(encoding="utf-8"))
    eventos = [
        json.loads(linha)
        for linha in resumo.caminho_eventos.read_text(encoding="utf-8").splitlines()
    ]
    etapas = {e["etapa"]: e for e in relatorio["etapas"]}
    return resumo, etapas, eventos


def test_prescricoes_detecta_mudanca_de_dose_e_alta_vigilancia(execucao):
    _, etapas, eventos = execucao
    anomalias = etapas["prescricoes"]["detalhes"]["anomalias"]
    assert "mudanca_abrupta_dose:dose.variacao" in anomalias
    assert any(a.startswith("medicamento_alta_vigilancia:ismp.opioide") for a in anomalias)
    tipos = {e["event_type"] for e in eventos if e["modality"] == "prescricoes"}
    assert {"mudanca_abrupta_dose", "medicamento_alta_vigilancia"} <= tipos


def test_movimentacao_detecta_imobilidade_prolongada(execucao):
    _, etapas, eventos = execucao
    assert etapas["movimentacao"]["detalhes"]["anomalias"] == ["imobilidade_prolongada"]
    movimentacao = [e for e in eventos if e["modality"] == "movimentacao"]
    assert [e["event_type"] for e in movimentacao] == ["imobilidade_prolongada"]
    assert movimentacao[0]["context"]["bed_id"] == "UTI-07"


def test_fusao_recebe_achados_das_seis_modalidades(execucao):
    resumo, etapas, _ = execucao
    fusao = etapas["fusao"]["detalhes"]
    assert set(fusao["presentes"]) == {
        "video", "audio", "texto", "sinais_vitais", "prescricoes", "movimentacao"
    }
    assert fusao["ausentes"] == []
    assert resumo.nivel_risco_final == "alto"


def test_eventos_das_etapas_usam_o_paciente_do_pipeline(execucao):
    resumo, _, eventos = execucao
    assert {e["patient_id"] for e in eventos} == {resumo.paciente_id}


def test_video_real_sem_transicoes_nao_inventa_achado(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from pipeline.etapas.video import EtapaVideo

    video = tmp_path / "leito.mp4"
    video.write_bytes(b"x")
    chamadas = []

    def falso_yolo(self, contexto, caminho, agora):
        chamadas.append(caminho)
        return [], SimpleNamespace(quadros_lidos=50, deteccoes_total=350)

    monkeypatch.setattr(EtapaVideo, "_processar_yolo", falso_yolo)
    cfg = ConfigPipeline.from_env(
        modo="real", fator_velocidade=0.0, saida_dir=tmp_path / "saida",
        sinais_fonte="sintetico", video_path=video,
    )
    resumo = OrquestradorPipeline(cfg).executar()
    relatorio = json.loads(resumo.caminho_relatorio.read_text(encoding="utf-8"))
    etapa = next(e for e in relatorio["etapas"] if e["etapa"] == "video")

    assert chamadas == [video]
    assert etapa["detalhes"]["modo"] == "yolo_real"
    assert etapa["eventos_emitidos"] == 0


def test_video_real_que_falha_cai_no_achado_de_demonstracao(tmp_path):
    video = tmp_path / "leito.mp4"
    video.write_bytes(b"nao e um video")
    cfg = ConfigPipeline.from_env(
        modo="real", fator_velocidade=0.0, saida_dir=tmp_path / "saida",
        sinais_fonte="sintetico", video_path=video,
    )
    resumo = OrquestradorPipeline(cfg).executar()
    relatorio = json.loads(resumo.caminho_relatorio.read_text(encoding="utf-8"))
    etapa = next(e for e in relatorio["etapas"] if e["etapa"] == "video")
    assert etapa["detalhes"]["modo"] == "clinico_leito"
    assert "motivo_fallback" in etapa["detalhes"]
    assert etapa["eventos_emitidos"] == 1


def test_demo_roda_sem_pseudonym_key_no_ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("PSEUDONYM_KEY", "")
    cfg = ConfigPipeline.from_env(fator_velocidade=0.0, saida_dir=tmp_path, sinais_fonte="sintetico")
    resumo = OrquestradorPipeline(cfg).executar()
    assert resumo.etapas_com_falha == ()


def test_alertas_por_email_leem_o_ambiente_e_nunca_usam_cosmos(tmp_path, monkeypatch):
    from pipeline.orquestrador import config_alertas

    monkeypatch.setenv("SMTP_HOST", "smtp.exemplo")
    monkeypatch.setenv("SMTP_REMETENTE", "alertas@exemplo")
    monkeypatch.setenv("ALERTA_DESTINATARIOS", "a@exemplo,b@exemplo")
    monkeypatch.setenv("ALERTA_STORE", "cosmos")
    cfg = ConfigPipeline.from_env(saida_dir=tmp_path, notificar_smtp=True)

    alertas = config_alertas(cfg, tmp_path)

    assert alertas.email_configurado
    assert alertas.destinatarios == ["a@exemplo", "b@exemplo"] or tuple(alertas.destinatarios) == (
        "a@exemplo", "b@exemplo")
    assert alertas.armazenamento == "jsonl"
    assert alertas.saida_dir == tmp_path


def test_sem_notificar_o_email_fica_desligado_mesmo_com_smtp_no_ambiente(tmp_path, monkeypatch):
    from pipeline.orquestrador import config_alertas

    monkeypatch.setenv("SMTP_HOST", "smtp.exemplo")
    monkeypatch.setenv("SMTP_REMETENTE", "alertas@exemplo")
    monkeypatch.setenv("ALERTA_DESTINATARIOS", "a@exemplo")
    cfg = ConfigPipeline.from_env(saida_dir=tmp_path)

    assert not config_alertas(cfg, tmp_path).email_configurado
