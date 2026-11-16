# Mantém a raiz do repositório no sys.path para `import contratos` nos testes.
# Se o pyproject.toml da US01 já tiver `[tool.pytest.ini_options] pythonpath = ["."]`, este arquivo pode sair.

import pytest


@pytest.fixture(autouse=True)
def _sem_envio_ao_blob_storage(monkeypatch):
    """Desliga a cópia das transcrições no Blob Storage em todos os testes.

    ``app.main`` roda ``load_dotenv()`` no import: sem isto, a connection string do ``.env`` real
    faria os testes de rota enviarem para a storage de verdade. Teste que quer o envio injeta um
    arquivador falso.
    """
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
