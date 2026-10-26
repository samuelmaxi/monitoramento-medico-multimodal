"""US11 → US13: o JSON da transcrição alimenta a análise, direto ou por POST /audio/analysis."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import transcription_service
from app.routers import audio_analysis, text_analysis
from app.routers import transcription as router_transcription
from app.transcription_client import TranscricaoError
from app.transcription_format import formatar_transcricao
from audio.text.analyze import analyze_transcript
from audio.text.language_client import LanguageClient
from audio.text.transcript import TranscriptError, transcript_from_dict
from contratos import BarramentoLocal

from .conftest import FakeTextAnalytics

REGRAS = {"dor no peito": {"normalized": "Chest Pain", "certainty": "positive"}}

# Resposta da transcrição rápida: falante 1 é o médico, falante 2 o paciente.
RESPOSTA_SPEECH = {
    "durationMilliseconds": 7000,
    "phrases": [
        {"speaker": 1, "text": "O senhor sente dor no peito?", "offsetMilliseconds": 0,
         "durationMilliseconds": 2000, "confidence": 0.93, "words": []},
        {"speaker": 2, "text": "Sim, estou com dor no peito desde ontem e com muito medo.",
         "offsetMilliseconds": 2500, "durationMilliseconds": 4000, "confidence": 0.9, "words": []},
    ],
}


def _documento_us11(**sobrescrever):
    opcoes = {"medico_falante": 1, "audio_id": "consulta-us11", "patient_id": "sim-paciente-us11",
              "recorded_at": "2026-10-01T13:00:00Z", **sobrescrever}
    return formatar_transcricao(
        RESPOSTA_SPEECH, nome_arquivo="consulta.wav", sha256="x", tamanho_bytes=1,
        max_speakers=2, frases_medicas=False, **opcoes,
    )


# Leitura do JSON da US11 -----------------------------------------------------

def test_falas_da_us11_viram_segmentos_com_o_papel_como_falante():
    transcricao = transcript_from_dict(_documento_us11())

    assert transcricao.audio_id == "consulta-us11"
    assert [(s.speaker, s.offset_ms, s.duration_ms) for s in transcricao.segments] == [
        ("medico", 0, 2000), ("paciente", 2500, 4000),
    ]
    assert transcricao.segments[1].confidence == 0.9


def test_transcricao_sem_medico_falante_e_recusada():
    with pytest.raises(TranscriptError, match="medico_falante"):
        transcript_from_dict(_documento_us11(medico_falante=None))


@pytest.mark.parametrize("campo", ["patient_id", "recorded_at", "audio_id"])
def test_metadado_nulo_conta_como_ausente(campo):
    with pytest.raises(TranscriptError, match=campo):
        transcript_from_dict(_documento_us11(**{campo: None}))


def test_fala_sem_falante_na_diarizacao_e_ignorada(config, fake_factory):
    documento = _documento_us11()
    documento["falas"][0]["papel"] = None
    fake, cliente = fake_factory(REGRAS)

    resultado = analyze_transcript(transcript_from_dict(documento), config, cliente)

    assert resultado.details["falas"]["desconhecido"] == 1
    assert [d["text"] for c in fake.health_calls for d in c["documents"]] == [
        RESPOSTA_SPEECH["phrases"][1]["text"]
    ]


# Rotas ------------------------------------------------------------------------

@pytest.fixture
def api(config, tmp_path, monkeypatch):
    """TestClient com Speech e Language falsos; devolve (client, fake do Language, chamadas ao Speech)."""
    fake = FakeTextAnalytics(REGRAS)
    chamadas_speech: list[dict] = []

    def speech_falso(audio_path, **kwargs):
        chamadas_speech.append(kwargs)
        return RESPOSTA_SPEECH

    monkeypatch.setattr(transcription_service, "transcrever_arquivo", speech_falso)
    monkeypatch.setattr(router_transcription, "TRANSCRIPTION_OUTPUT_DIR", str(tmp_path / "transcricao"))
    monkeypatch.setattr(router_transcription, "require_azure_speech_key", lambda: "chave")
    monkeypatch.setattr(router_transcription, "require_azure_speech_endpoint", lambda: "https://fake")
    monkeypatch.setattr(text_analysis, "get_text_config", lambda: config)
    monkeypatch.setattr(
        text_analysis, "get_language_client", lambda: LanguageClient(fake, config.azure, sleep=lambda _s: None)
    )
    monkeypatch.setattr(text_analysis, "get_barramento", lambda: BarramentoLocal(tmp_path / "eventos.jsonl"))
    app = FastAPI()
    app.include_router(text_analysis.router)
    app.include_router(audio_analysis.router)
    return TestClient(app), fake, chamadas_speech


def _enviar_audio(client, **campos):
    dados = {"patient_id": "10000032", "recorded_at": "2026-10-01T13:00:00Z", "medico_falante": "1", **campos}
    dados = {k: v for k, v in dados.items() if v is not None}
    return client.post(
        "/audio/analysis",
        files={"audio_file": ("consulta.wav", b"RIFF-consulta", "audio/wav")},
        data=dados,
    )


def test_text_analysis_aceita_o_json_da_us11(api):
    client, fake, _ = api

    resp = client.post("/text/analysis", json=_documento_us11())

    assert resp.status_code == 200, resp.text
    tipos = sorted(e["event_type"] for e in resp.json()["eventos"])
    assert tipos == ["sentimento_negativo", "termo_critico"]
    assert len(fake.health_calls) == 1


def test_text_analysis_com_us11_sem_papeis_da_422(api):
    client, fake, _ = api

    resp = client.post("/text/analysis", json=_documento_us11(medico_falante=None))

    assert resp.status_code == 422
    assert "medico_falante" in resp.json()["detail"]
    assert fake.health_calls == []


def test_audio_analysis_transcreve_e_analisa_so_a_fala_do_paciente(api, tmp_path):
    client, fake, chamadas_speech = api

    resp = _enviar_audio(client, audio_id="consulta-07", detalhes=None)

    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["audio_id"] == "consulta-07"
    assert corpo["patient_id"].startswith("pt_")
    assert corpo["transcricao"]["patient_id"] == "10000032"
    assert [f["papel"] for f in corpo["transcricao"]["falas"]] == ["medico", "paciente"]
    assert len(chamadas_speech) == 1
    assert [d["text"] for c in fake.health_calls for d in c["documents"]] == [
        RESPOSTA_SPEECH["phrases"][1]["text"]
    ]
    termo = next(e for e in corpo["eventos"] if e["event_type"] == "termo_critico")
    assert termo["evidence"]["features"]["pergunta_medico"] == "O senhor sente dor no peito?"
    # recorded_at + offset da fala do paciente
    assert termo["timestamp"].startswith("2026-10-01T13:00:0")
    assert any(e["event_type"] == "sentimento_negativo" for e in corpo["eventos"])
    assert list((tmp_path / "transcricao").glob("transcription-*/audio.json"))


def test_audio_analysis_com_detalhes_traz_o_sentimento_por_sentenca(api):
    client, _, _ = api

    resp = client.post(
        "/audio/analysis",
        params={"detalhes": "true"},
        files={"audio_file": ("consulta.wav", b"RIFF", "audio/wav")},
        data={"patient_id": "10000032", "recorded_at": "2026-10-01T13:00:00Z", "medico_falante": "1"},
    )

    assert resp.status_code == 200, resp.text
    assert "sentimento" in resp.json()["detalhes"]


@pytest.mark.parametrize("campo", ["medico_falante", "patient_id", "recorded_at"])
def test_audio_analysis_exige_os_campos_da_consulta(api, campo):
    client, _, chamadas_speech = api

    resp = _enviar_audio(client, **{campo: None})

    assert resp.status_code == 422
    assert chamadas_speech == []


def test_audio_analysis_sem_chave_de_pseudonimizacao_nao_chama_o_speech(api, monkeypatch):
    client, _, chamadas_speech = api
    monkeypatch.delenv("PSEUDONYM_KEY")

    resp = _enviar_audio(client)

    assert resp.status_code == 503
    assert "PSEUDONYM_KEY" in resp.json()["detail"]
    assert chamadas_speech == []


def test_audio_analysis_com_falha_do_speech_preserva_o_audio(api, monkeypatch, tmp_path):
    client, fake, _ = api

    def speech_fora(audio_path, **kwargs):
        raise TranscricaoError("Falhou após 3 tentativas: ReadTimeout", None, 3)

    monkeypatch.setattr(transcription_service, "transcrever_arquivo", speech_fora)

    resp = _enviar_audio(client)

    assert resp.status_code == 504
    assert "preservado" in resp.json()["detail"]
    assert list((tmp_path / "transcricao").glob("transcription-*/audio.wav"))
    assert fake.health_calls == []
