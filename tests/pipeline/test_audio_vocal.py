"""Biomarcadores vocais (US12/US14) dentro da etapa de áudio do pipeline (US20)."""

import json

import numpy as np
import pytest
import soundfile as sf

from pipeline import ConfigPipeline, OrquestradorPipeline


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.setenv("PSEUDONYM_KEY", "chave-de-teste-com-mais-de-32-bytes-000000")
    for var in ("AZURE_SPEECH_KEY", "AZURE_SPEECH_ENDPOINT", "AZURE_LANGUAGE_KEY", "AZURE_LANGUAGE_ENDPOINT"):
        monkeypatch.delenv(var, raising=False)


def _executar(tmp_path, **config):
    cfg = ConfigPipeline.from_env(
        fator_velocidade=0.0, saida_dir=tmp_path / "saida", sinais_fonte="sintetico", **config
    )
    resumo = OrquestradorPipeline(cfg).executar()
    relatorio = json.loads(resumo.caminho_relatorio.read_text(encoding="utf-8"))
    return resumo, next(e for e in relatorio["etapas"] if e["etapa"] == "audio")


def test_sem_audio_usa_achado_vocal_de_demonstracao(tmp_path):
    _, etapa = _executar(tmp_path)
    assert "sem áudio" in etapa["detalhes"]["biomarcadores_vocais"]


def test_com_audio_roda_audio_vocal_e_publica_no_barramento(tmp_path):
    t = np.arange(16000 * 4) / 16000
    wav = tmp_path / "paciente.wav"
    sf.write(str(wav), (0.4 * np.sin(2 * np.pi * 180 * t)).astype(np.float32), 16000)

    resumo, etapa = _executar(tmp_path, audio_path=wav)

    assert etapa["status"] == "sucesso"
    assert etapa["detalhes"]["biomarcadores_vocais"].startswith("audio.vocal")
    assert etapa["detalhes"]["eventos_vocais"] >= 1
    assert (tmp_path / "saida" / "audio" / "paciente" / "relatorio.json").is_file()
    linhas = resumo.caminho_eventos.read_text(encoding="utf-8").splitlines()
    assert any(json.loads(linha)["modality"] == "audio" for linha in linhas)


def test_audio_invalido_cai_no_achado_de_demonstracao_e_registra_o_motivo(tmp_path):
    wav = tmp_path / "quebrado.wav"
    wav.write_bytes(b"isto nao e um wav")
    _, etapa = _executar(tmp_path, audio_path=wav)
    assert etapa["status"] == "sucesso"
    assert "falhou" in etapa["detalhes"]["biomarcadores_vocais"]


def _eventos(resumo) -> list[dict]:
    if not resumo.caminho_eventos.is_file():  # nada publicado: o barramento não cria o arquivo
        return []
    return [json.loads(x) for x in resumo.caminho_eventos.read_text(encoding="utf-8").splitlines()]


def _speech_falso(monkeypatch, falas):
    """Azure Speech falso: a diarização devolve ``falas`` = [(falante, texto, offset_ms, duracao_ms)]."""
    from app import transcription_service

    monkeypatch.setenv("AZURE_SPEECH_KEY", "chave-falsa")
    monkeypatch.setenv("AZURE_SPEECH_ENDPOINT", "https://falso.cognitiveservices.azure.com")
    resposta = {
        "durationMilliseconds": 4000,
        "phrases": [
            {"speaker": falante, "text": texto, "offsetMilliseconds": inicio,
             "durationMilliseconds": duracao, "confidence": 0.9, "locale": "pt-BR",
             "words": [{"text": p, "offsetMilliseconds": inicio, "durationMilliseconds": 200}
                       for p in texto.split()]}
            for falante, texto, inicio, duracao in falas
        ],
    }
    monkeypatch.setattr(transcription_service, "transcrever_arquivo", lambda *a, **k: resposta)


def _language_falso(monkeypatch):
    """Azure AI Language "real" com o SDK de demonstração por baixo (sem rede)."""
    from audio.text.language_client import LanguageClient
    from pipeline.simulacao_azure import LanguageSdkSimulado

    monkeypatch.setenv("AZURE_LANGUAGE_KEY", "chave-falsa")
    monkeypatch.setenv("AZURE_LANGUAGE_ENDPOINT", "https://falso.cognitiveservices.azure.com")
    monkeypatch.setattr(
        LanguageClient, "from_env", classmethod(lambda cls, cfg: cls(LanguageSdkSimulado(), cfg))
    )


def _audio_do_paciente(tmp_path):
    t = np.arange(16000 * 4) / 16000
    wav = tmp_path / "paciente.wav"
    sf.write(str(wav), (0.4 * np.sin(2 * np.pi * 180 * t)).astype(np.float32), 16000)
    return wav


FALAS_SO_PACIENTE = [
    (1, "Estou com muita falta de ar desde ontem", 0, 1800),
    (1, "e um aperto no peito quando respiro", 2000, 1800),
]


def test_modo_real_sem_entradas_nao_publica_nenhum_achado(tmp_path):
    """Nada enviado, nada inventado: as modalidades ficam ausentes, não viram demonstração."""
    cfg = ConfigPipeline.from_env(modo="real", fator_velocidade=0.0, saida_dir=tmp_path / "saida")
    resumo = OrquestradorPipeline(cfg).executar()
    relatorio = json.loads(resumo.caminho_relatorio.read_text(encoding="utf-8"))

    assert [e["etapa"] for e in relatorio["etapas"]] == ["fusao", "alertas"]
    assert set(resumo.etapas_ausentes) == {
        "video", "relatorio_video", "audio", "sinais_vitais", "prescricoes", "movimentacao"
    }
    assert all(a["motivo"] for a in relatorio["etapas_ausentes"])
    assert _eventos(resumo) == []
    assert resumo.risco is None and resumo.nivel_risco_final == "indisponivel"
    assert resumo.etapas_com_falha == ()


def test_modo_real_so_com_audio_nao_tem_achado_sintetico(tmp_path, monkeypatch):
    _speech_falso(monkeypatch, FALAS_SO_PACIENTE)
    _language_falso(monkeypatch)
    cfg = ConfigPipeline.from_env(
        modo="real", fator_velocidade=0.0, saida_dir=tmp_path / "saida",
        audio_path=_audio_do_paciente(tmp_path), medico_falante=0,
    )
    resumo = OrquestradorPipeline(cfg).executar()
    relatorio = json.loads(resumo.caminho_relatorio.read_text(encoding="utf-8"))

    assert [e["etapa"] for e in relatorio["etapas"]] == ["audio", "fusao", "alertas"]
    eventos = _eventos(resumo)
    assert eventos and not any(e["is_synthetic"] for e in eventos)
    assert {e["modality"] for e in eventos} <= {"audio", "texto", "fusao"}
    assert {m.value for m in resumo.risco.ausentes} >= {
        "video", "sinais_vitais", "prescricoes", "movimentacao"
    }


def test_gravacao_so_do_paciente_com_medico_falante_0_analisa_as_falas(tmp_path, monkeypatch):
    _speech_falso(monkeypatch, FALAS_SO_PACIENTE)
    _language_falso(monkeypatch)

    resumo, etapa = _executar(
        tmp_path, modo="real", audio_path=_audio_do_paciente(tmp_path), medico_falante=0
    )

    detalhes = etapa["detalhes"]
    assert detalhes["servico_speech"] == "Azure AI Speech (transcrição rápida, real)"
    assert detalhes["servico_language"] == "Azure AI Language (real)"
    assert detalhes["medico_falante"] == "nenhum (gravação só do paciente)"
    assert detalhes["falantes_diarizacao"] == 1
    assert detalhes["falas_do_paciente"] == 2
    assert detalhes["biomarcadores_vocais"].startswith("audio.vocal")
    assert any(e["event_type"] == "termo_critico" and not e["is_synthetic"] for e in _eventos(resumo))
    assert detalhes["armazenamento_transcricao"].startswith("desligado")  # sem connection string


def test_transcricao_do_pipeline_e_copiada_para_o_blob_storage(tmp_path, monkeypatch, blob_falso):
    _speech_falso(monkeypatch, FALAS_SO_PACIENTE)
    _language_falso(monkeypatch)

    _, etapa = _executar(
        tmp_path, modo="real", audio_path=_audio_do_paciente(tmp_path), medico_falante=0
    )

    # Nome fixo: o do arquivo enviado (paciente.wav) não vai para a nuvem.
    assert etapa["detalhes"]["armazenamento_transcricao"] == (
        "enviado: audio-transcripts/pipeline/saida/consulta.json"
    )
    (envio,) = blob_falso.envios
    gravado = tmp_path / "saida" / "transcricao" / "paciente.json"
    assert envio["dados"] == gravado.read_bytes()


def test_falha_no_blob_storage_nao_derruba_a_etapa_de_audio(tmp_path, monkeypatch, blob_falso):
    _speech_falso(monkeypatch, FALAS_SO_PACIENTE)
    _language_falso(monkeypatch)
    blob_falso.erro = ConnectionError("storage fora do ar")

    _, etapa = _executar(
        tmp_path, modo="real", audio_path=_audio_do_paciente(tmp_path), medico_falante=0
    )

    assert etapa["status"] == "sucesso"
    assert etapa["detalhes"]["armazenamento_transcricao"].startswith("falhou: ConnectionError")
    assert etapa["detalhes"]["falas_do_paciente"] == 2


def test_gravacao_so_do_paciente_com_medico_falante_1_falha_sem_inventar_achado(
    tmp_path, monkeypatch
):
    """O caso que motivou o 0: a única voz vira "médico" e não sobra fala do paciente."""
    _speech_falso(monkeypatch, FALAS_SO_PACIENTE)
    _language_falso(monkeypatch)

    resumo, etapa = _executar(tmp_path, modo="real", audio_path=_audio_do_paciente(tmp_path))

    assert etapa["detalhes"]["falas_do_paciente"] == 0
    assert "falhou" in etapa["detalhes"]["biomarcadores_vocais"]
    assert "sintético" not in etapa["detalhes"]["biomarcadores_vocais"]
    assert not any(e["is_synthetic"] for e in _eventos(resumo))


def test_modo_real_sem_chaves_do_speech_mede_a_voz_e_diz_o_que_nao_rodou(tmp_path):
    resumo, etapa = _executar(
        tmp_path, modo="real", audio_path=_audio_do_paciente(tmp_path), medico_falante=0
    )

    detalhes = etapa["detalhes"]
    assert etapa["status"] == "sucesso"
    assert detalhes["servico_speech"].startswith("não usado")
    assert "servico_language" not in detalhes  # sem transcrição, o texto nem é tentado
    assert detalhes["biomarcadores_vocais"].startswith("audio.vocal")  # VAD: só o paciente fala
    assert not any(e["is_synthetic"] for e in _eventos(resumo))


def test_modo_real_sem_transcricao_e_com_medico_nao_mede_a_voz(tmp_path):
    """Sem diarização, a voz do médico seria medida como a do paciente: a etapa falha."""
    _, etapa = _executar(tmp_path, modo="real", audio_path=_audio_do_paciente(tmp_path))

    assert etapa["status"] == "falha"
    assert etapa["detalhes"]["biomarcadores_vocais"].startswith("não analisado")
    assert etapa["eventos_emitidos"] == 0
