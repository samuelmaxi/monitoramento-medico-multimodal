import pytest
from fastapi.testclient import TestClient

from app import transcription_service
from app.main import app
from app.routers import transcription as router_transcription
from app.transcription_client import TranscricaoError

RESPOSTA_API = {
    "durationMilliseconds": 2000,
    "phrases": [
        {
            "speaker": 1,
            "text": "Como está se sentindo?",
            "offsetMilliseconds": 0,
            "durationMilliseconds": 2000,
            "confidence": 0.9,
            "words": [],
        }
    ],
}


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(router_transcription, "TRANSCRIPTION_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(router_transcription, "require_azure_speech_key", lambda: "chave")
    monkeypatch.setattr(
        router_transcription, "require_azure_speech_endpoint", lambda: "https://fake"
    )
    return TestClient(app)


def _enviar(cliente, nome="consulta.wav", **campos):
    return cliente.post(
        "/transcription",
        files={"audio_file": (nome, b"RIFF-consulta", "audio/wav")},
        data=campos,
    )


def test_transcricao_bem_sucedida_devolve_json_e_grava_audio_e_resultado(
    cliente, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        transcription_service, "transcrever_arquivo", lambda audio_path, **kw: RESPOSTA_API
    )

    resposta = _enviar(cliente)

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["texto_completo"] == "Como está se sentindo?"
    assert corpo["falas"][0]["falante"] == 1
    pasta = next(tmp_path.glob("transcription-*"))
    assert (pasta / "audio.wav").read_bytes() == b"RIFF-consulta"
    assert (pasta / "audio.json").is_file()


def test_falha_da_api_devolve_erro_e_preserva_o_audio(cliente, tmp_path, monkeypatch):
    def api_fora(audio_path, **kwargs):
        raise TranscricaoError("Falhou após 3 tentativas: ReadTimeout", None, 3)

    monkeypatch.setattr(transcription_service, "transcrever_arquivo", api_fora)

    resposta = _enviar(cliente)

    assert resposta.status_code == 504
    assert "preservado" in resposta.json()["detail"]
    audio = next(tmp_path.glob("transcription-*/audio.wav"))
    assert audio.read_bytes() == b"RIFF-consulta"
    assert not list(tmp_path.glob("transcription-*/audio.json"))


@pytest.mark.parametrize(
    ("status_api", "status_http"),
    [(401, 401), (429, 429), (400, 502)],
)
def test_erros_do_azure_viram_status_http(cliente, monkeypatch, status_api, status_http):
    def api_erro(audio_path, **kwargs):
        raise TranscricaoError("erro do azure", status_api, 1)

    monkeypatch.setattr(transcription_service, "transcrever_arquivo", api_erro)

    assert _enviar(cliente).status_code == status_http


def test_formato_nao_suportado_e_rejeitado_antes_de_gravar(cliente, tmp_path):
    resposta = _enviar(cliente, nome="video.mp4")

    assert resposta.status_code == 400
    assert not list(tmp_path.glob("transcription-*"))


def test_metadados_e_papeis_vao_para_a_transcricao(cliente, tmp_path, monkeypatch):
    monkeypatch.setattr(
        transcription_service, "transcrever_arquivo", lambda audio_path, **kw: RESPOSTA_API
    )

    resposta = _enviar(
        cliente, patient_id="10000032", recorded_at="2026-10-01T13:00:00Z", medico_falante="1"
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    pasta = next(tmp_path.glob("transcription-*"))
    assert corpo["audio_id"] == pasta.name
    assert corpo["patient_id"] == "10000032"
    assert corpo["recorded_at"] == "2026-10-01T13:00:00Z"
    assert corpo["falas"][0]["papel"] == "medico"


def test_recorded_at_fora_de_utc_da_422_sem_gravar_o_audio(cliente, tmp_path):
    resposta = _enviar(cliente, recorded_at="2026-10-01T10:00:00-03:00")

    assert resposta.status_code == 422
    assert "UTC" in resposta.json()["detail"]
    assert not list(tmp_path.glob("transcription-*"))
