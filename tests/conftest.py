import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

_REQUIRED_ENV_VARS = [
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_REGION",
    "AWS_S3_BUCKET_NAME",
]


@pytest.fixture(scope="session", autouse=True)
def _require_aws_env():
    missing = [var for var in _REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing:
        pytest.skip(
            "Variáveis de ambiente AWS ausentes para o smoke test: "
            f"{missing}. Configure o .env antes de rodar estes testes."
        )


@pytest.fixture
def client():
    return TestClient(app)
