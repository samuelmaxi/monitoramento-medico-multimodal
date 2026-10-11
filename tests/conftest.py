import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

_REQUIRED_ENV_VARS = [
    "AZURE_SPEECH_KEY",
    "AZURE_SPEECH_REGION",
    "AZURE_LANGUAGE_ENDPOINT",
    "AZURE_LANGUAGE_KEY",
    "AZURE_STORAGE_CONNECTION_STRING",
]


@pytest.fixture(scope="session", autouse=True)
def _require_azure_env():
    missing = [var for var in _REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing:
        pytest.skip(
            "Variáveis de ambiente Azure ausentes para o smoke test: "
            f"{missing}. Configure o .env antes de rodar estes testes."
        )


@pytest.fixture
def client():
    return TestClient(app)
