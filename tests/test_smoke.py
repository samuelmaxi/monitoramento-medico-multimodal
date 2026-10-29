import os
from pathlib import Path

import pytest

# Smoke tests dependem de Azure real: pulam se as variáveis não estiverem configuradas.
pytestmark = pytest.mark.usefixtures("_require_azure_env")

SMOKE_TEST_AUDIO_PATH = Path(
    os.getenv("SMOKE_TEST_AUDIO_PATH", "tests/fixtures/sample_pt_br.wav")
)


def test_sentiment_analysis_pt_returns_200(client, require_aws_env):
    response = client.post(
        "/sentiment",
        json={
            "text": "Estou muito satisfeito com o atendimento recebido.",
            "language_code": "pt",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sentiment"] in {"POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED"}


def test_transcription_pt_br_returns_200(client, require_aws_env):
    if not SMOKE_TEST_AUDIO_PATH.is_file():
        pytest.skip(
            f"Áudio de teste não encontrado em '{SMOKE_TEST_AUDIO_PATH}'. "
            "Veja tests/fixtures/README.md para instruções."
        )

    with SMOKE_TEST_AUDIO_PATH.open("rb") as audio_file:
        response = client.post(
            "/transcription",
            files={"audio_file": (SMOKE_TEST_AUDIO_PATH.name, audio_file, "audio/wav")},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["idioma"] == "pt-BR"
    assert body["texto_completo"]
    assert all("falante" in fala and "confianca" in fala for fala in body["falas"])
