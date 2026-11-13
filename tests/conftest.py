import os
from pathlib import Path

import pytest

try:
    from fastapi.testclient import TestClient

    from app.main import app
except ModuleNotFoundError as erro:
    # Ambiente leve (pip install -r video/requirements.txt, sem uv sync): a API não importa,
    # mas os testes que não a usam rodam. Só tolera dependência de TERCEIROS ausente; um módulo
    # do próprio projeto que falte continua sendo erro (senão um import quebrado viraria skip).
    _PACOTES_DO_PROJETO = {
        pasta.name for pasta in Path(__file__).resolve().parents[1].iterdir()
        if (pasta / "__init__.py").is_file()
    }
    if (erro.name or "").split(".")[0] in _PACOTES_DO_PROJETO:
        raise
    TestClient = app = None
    _ERRO_DA_API = erro

_REQUIRED_ENV_VARS = [
    "AZURE_SPEECH_KEY",
    "AZURE_SPEECH_REGION",
    "AZURE_LANGUAGE_ENDPOINT",
    "AZURE_LANGUAGE_KEY",
    "AZURE_STORAGE_CONNECTION_STRING",
]


@pytest.fixture(scope="session")
def _require_azure_env():
    missing = [var for var in _REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing:
        pytest.skip(
            "Variáveis de ambiente Azure ausentes para o smoke test: "
            f"{missing}. Configure o .env antes de rodar estes testes."
        )
    return None


@pytest.fixture
def client():
    if app is None:
        pytest.skip(f"a API não pôde ser importada neste ambiente ({_ERRO_DA_API}); rode uv sync")
    return TestClient(app)
