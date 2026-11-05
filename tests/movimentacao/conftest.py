from datetime import UTC, datetime

import pytest

from movimentacao.config import carregar_config
from movimentacao.modelos import Observacao, Posicao


def instante(hhmm: str, dia: int = 1) -> datetime:
    h, m = hhmm.split(":")[:2]
    s = hhmm.split(":")[2] if hhmm.count(":") == 2 else "0"
    return datetime(2026, 10, dia, int(h), int(m), int(s), tzinfo=UTC)


def obs(hhmm: str, posicao: Posicao, dia: int = 1, fonte: str = "teste", **kw) -> Observacao:
    return Observacao(instante=instante(hhmm, dia), posicao=posicao, fonte=fonte, **kw)


@pytest.fixture(scope="session")
def cfg():
    return carregar_config()


CHAVE_TESTE = "chave-de-teste-com-mais-de-32-bytes-000000"


@pytest.fixture
def chave(monkeypatch):
    monkeypatch.setenv("PSEUDONYM_KEY", CHAVE_TESTE)
    return CHAVE_TESTE
