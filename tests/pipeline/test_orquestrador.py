"""Testes de integração da US20: fluxo completo em modo simulado, sem rede nem chaves."""

import json

import pytest

from pipeline import ConfigPipeline, OrquestradorPipeline

ETAPAS = ["video", "audio", "sinais_vitais", "prescricoes", "movimentacao", "fusao", "alertas"]


@pytest.fixture(autouse=True)
def _sem_credenciais_azure(monkeypatch):
    for var in ("AZURE_SPEECH_KEY", "AZURE_SPEECH_ENDPOINT", "AZURE_LANGUAGE_KEY", "AZURE_LANGUAGE_ENDPOINT"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def execucao(tmp_path):
    config = ConfigPipeline.from_env(
        modo="simulado", fator_velocidade=0.0, saida_dir=tmp_path, sinais_fonte="sintetico"
    )
    resumo = OrquestradorPipeline(config).executar()
    relatorio = json.loads(resumo.caminho_relatorio.read_text(encoding="utf-8"))
    return resumo, relatorio


def test_fluxo_completo_executa_todas_as_etapas_em_ordem(execucao):
    _, relatorio = execucao
    assert [e["etapa"] for e in relatorio["etapas"]] == ETAPAS
    assert all(e["status"] == "sucesso" for e in relatorio["etapas"])


def test_fusao_gera_risco_alto_e_alerta(execucao):
    resumo, _ = execucao
    assert resumo.nivel_risco_final == "alto"
    assert resumo.total_alertas > 0
    assert resumo.caminho_alertas is not None and resumo.caminho_alertas.is_file()


def test_barramento_recebe_achados_de_todas_as_modalidades(execucao):
    resumo, _ = execucao
    linhas = resumo.caminho_eventos.read_text(encoding="utf-8").splitlines()
    modalidades = {json.loads(linha)["modality"] for linha in linhas}
    assert {"video", "audio", "texto", "sinais_vitais", "prescricoes", "fusao"} <= modalidades
    assert len(linhas) == resumo.total_eventos_barramento


def test_log_estruturado_registra_tempo_por_etapa(execucao):
    _, relatorio = execucao
    for etapa in relatorio["etapas"]:
        assert etapa["duracao_ms"] >= 0
        assert etapa["inicio_utc"] <= etapa["fim_utc"]
    assert relatorio["duracao_total_ms"] == pytest.approx(
        sum(e["duracao_ms"] for e in relatorio["etapas"]), abs=0.1
    )


def test_azure_speech_e_language_rodam_dentro_do_fluxo_de_audio(execucao):
    _, relatorio = execucao
    audio = next(e for e in relatorio["etapas"] if e["etapa"] == "audio")
    assert "Speech" in audio["detalhes"]["servico_speech"]
    assert "Language" in audio["detalhes"]["servico_language"]
    assert audio["detalhes"]["eventos_texto"] > 0


def test_streaming_de_sinais_detecta_deterioracao_gradual(execucao):
    _, relatorio = execucao
    sinais = next(e for e in relatorio["etapas"] if e["etapa"] == "sinais_vitais")
    assert sinais["detalhes"]["total_medicoes_streaming"] > 0
    assert sinais["detalhes"]["eventos_detectados"]


def test_modo_real_sem_credenciais_cai_na_transcricao_de_demonstracao(tmp_path):
    config = ConfigPipeline.from_env(modo="real", fator_velocidade=0.0, saida_dir=tmp_path)
    resumo = OrquestradorPipeline(config).executar()
    relatorio = json.loads(resumo.caminho_relatorio.read_text(encoding="utf-8"))
    audio = next(e for e in relatorio["etapas"] if e["etapa"] == "audio")
    assert "demonstração" in audio["detalhes"]["servico_speech"]


def test_modo_real_com_audio_chama_azure_speech(tmp_path, monkeypatch):
    audio = tmp_path / "consulta.wav"
    audio.write_bytes(b"RIFF")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "chave")
    monkeypatch.setenv("AZURE_SPEECH_ENDPOINT", "https://speech.exemplo")
    chamadas = []

    def falso_transcrever(caminho, saida_dir, **kwargs):
        chamadas.append((caminho, kwargs))
        return {
            "audio_id": kwargs["audio_id"],
            "patient_id": kwargs["patient_id"],
            "recorded_at": kwargs["recorded_at"],
            "medico_falante": kwargs["medico_falante"],
            "falas": [
                {"papel": "paciente", "texto": "Estou com aperto no peito.", "offset_ms": 0, "duracao_ms": 2000},
            ],
        }

    monkeypatch.setattr("app.transcription_service.transcrever_e_salvar", falso_transcrever)
    config = ConfigPipeline.from_env(
        modo="real", fator_velocidade=0.0, saida_dir=tmp_path / "saida", audio_path=audio
    )
    resumo = OrquestradorPipeline(config).executar()

    assert chamadas and chamadas[0][0] == audio
    relatorio = json.loads(resumo.caminho_relatorio.read_text(encoding="utf-8"))
    audio_etapa = next(e for e in relatorio["etapas"] if e["etapa"] == "audio")
    assert audio_etapa["detalhes"]["falas_transcritas"] == 1
    # só o texto do Speech chega ao Language; a transcrição de demonstração não pode vazar
    linhas = resumo.caminho_eventos.read_text(encoding="utf-8").splitlines()
    textos = [json.loads(linha) for linha in linhas]
    eventos = " ".join(str(e) for e in textos if e["modality"] == "texto").lower()
    assert "aperto no peito" in eventos
    assert "falta de ar" not in eventos
    assert "real" in audio_etapa["detalhes"]["servico_speech"]
