import os
import time
from pathlib import Path

import pytest

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
        start_response = client.post(
            "/transcription",
            files={"audio_file": (SMOKE_TEST_AUDIO_PATH.name, audio_file, "audio/wav")},
        )

    assert start_response.status_code == 200
    job_name = start_response.json()["job_name"]

    deadline = time.monotonic() + 120
    result = None
    while time.monotonic() < deadline:
        result_response = client.get(f"/transcription/{job_name}")
        assert result_response.status_code == 200
        result = result_response.json()
        if result["status"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(5)

    assert result is not None
    assert result["status"] == "COMPLETED", result.get("failure_reason")
    assert result["transcript"]
