"""Testes do cliente Azure AI Language: limites, lotes, retry, erros de credencial e cache."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError

from audio.text.analyze import analyze_transcript
from audio.text.language_client import LanguageAuthError, LanguageClient, LanguageServiceError, snake
from audio.text.segmentation import chunk_text

from .conftest import FakeTextAnalytics, consulta


def _http(status: int, retry_after: str | None = None) -> HttpResponseError:
    exc = HttpResponseError(message=f"HTTP {status}")
    exc.status_code = status
    exc.response = SimpleNamespace(headers={"Retry-After": retry_after} if retry_after else {})
    return exc


def _texto_longo(n_sentencas: int) -> str:
    return " ".join(f"Esta é a sentença número {i} de um relato muito longo do paciente." for i in range(n_sentencas))


def test_texto_acima_de_5120_e_dividido_por_sentenca():
    texto = _texto_longo(200)  # ~13 mil caracteres
    assert len(texto) > 5120

    chunks = chunk_text(texto, 5120)

    assert len(chunks) >= 3
    assert all(len(c.text) <= 5120 for c in chunks)
    assert all(texto[c.offset:c.end] == c.text for c in chunks)  # offsets voltam ao texto original
    assert all(c.text.endswith(".") for c in chunks)  # corta em fim de sentença
    assert " ".join(c.text for c in chunks) == texto


def test_sentenca_unica_maior_que_o_limite_e_cortada_em_espaco():
    texto = "palavra " * 1000 + "fim."
    chunks = chunk_text(texto.strip(), 5120)
    assert all(len(c.text) <= 5120 for c in chunks)
    assert all(not c.text.startswith(" ") and not c.text.endswith(" ") for c in chunks)


def test_fala_longa_respeita_limite_por_documento_e_lotes(config, fake_factory):
    fake, cliente = fake_factory({"falta de ar": {}})
    longa = _texto_longo(800) + " No fim do relato: sinto falta de ar ao subir a escada."
    t = consulta(("paciente", longa))

    resultado = analyze_transcript(t, config, cliente)

    docs_sentimento = [len(c["documents"]) for c in fake.sentiment_calls]
    docs_health = [len(c["documents"]) for c in fake.health_calls]
    assert sum(docs_sentimento) > 10 and max(docs_sentimento) <= 10  # lotes de até 10
    assert max(docs_health) <= 25
    assert all(len(d["text"]) <= 5120 for c in fake.sentiment_calls + fake.health_calls for d in c["documents"])
    evento, = [e for e in resultado.events if e.event_type == "termo_critico"]
    # offset da entidade no último pedaço volta para a posição certa na fala
    assert evento.evidence.features["offset_ms"] > t.segments[0].offset_ms + 0.9 * t.segments[0].duration_ms


def test_cliente_recusa_documento_acima_do_limite(fake_factory):
    _, cliente = fake_factory()
    with pytest.raises(ValueError, match="excede 5120"):
        cliente.analyze_sentiment(["x" * 5121])


def test_retry_exponencial_em_429(config):
    fake = FakeTextAnalytics(falhas=[_http(429), _http(429)])
    esperas: list[float] = []
    cliente = LanguageClient(fake, config.azure, sleep=esperas.append)

    resultado = cliente.analyze_sentiment(["Estou bem."])

    assert esperas == [1.0, 2.0]  # 1s, 2s (initial_delay_s * 2^n)
    assert resultado[0][0].sentiment == "neutral"
    assert cliente.requests_sent["sentiment"] == 3


def test_retry_respeita_retry_after(config):
    fake = FakeTextAnalytics(falhas=[_http(429, retry_after="7")])
    esperas: list[float] = []
    LanguageClient(fake, config.azure, sleep=esperas.append).analyze_sentiment(["Estou bem."])
    assert esperas == [7.0]


def test_429_persistente_vira_erro_claro(config):
    tentativas = config.azure.retry.max_attempts
    fake = FakeTextAnalytics(falhas=[_http(429)] * tentativas)
    cliente = LanguageClient(fake, config.azure, sleep=lambda _s: None)
    with pytest.raises(LanguageServiceError, match="429"):
        cliente.analyze_sentiment(["Estou bem."])


def test_job_do_health_parado_entra_no_retry(config):
    fake = FakeTextAnalytics(jobs_parados=1)
    esperas: list[float] = []
    cliente = LanguageClient(fake, config.azure, sleep=esperas.append)

    cliente.analyze_health(["Estou com febre."])

    assert fake.health_waits == [config.azure.health_timeout_s] * 2
    assert esperas == [1.0]
    assert cliente.requests_sent["health"] == 2


def test_job_do_health_sempre_parado_vira_erro_claro(config):
    tentativas = config.azure.retry.max_attempts
    fake = FakeTextAnalytics(jobs_parados=tentativas)
    cliente = LanguageClient(fake, config.azure, sleep=lambda _s: None)
    with pytest.raises(LanguageServiceError, match="health_timeout_s"):
        cliente.analyze_health(["Estou com febre."])


def test_401_gera_mensagem_clara_sem_retry(config):
    fake = FakeTextAnalytics(falhas=[ClientAuthenticationError(message="Access denied")])
    esperas: list[float] = []
    cliente = LanguageClient(fake, config.azure, sleep=esperas.append)
    with pytest.raises(LanguageAuthError, match="AZURE_LANGUAGE_KEY"):
        cliente.analyze_health(["Estou com febre."])
    assert esperas == []


def test_403_gera_mensagem_clara(config):
    fake = FakeTextAnalytics(falhas=[_http(403)])
    cliente = LanguageClient(fake, config.azure, sleep=lambda _s: None)
    with pytest.raises(LanguageAuthError, match="403"):
        cliente.analyze_sentiment(["Estou bem."])


def test_variavel_de_ambiente_ausente_gera_mensagem_clara(config, monkeypatch):
    monkeypatch.delenv("AZURE_LANGUAGE_ENDPOINT", raising=False)
    with pytest.raises(LanguageAuthError, match="AZURE_LANGUAGE_ENDPOINT"):
        LanguageClient.from_env(config.azure)


def test_cache_evita_nova_chamada(config, tmp_path):
    azure = dataclasses.replace(config.azure, cache_dir=tmp_path / "cache")
    fake = FakeTextAnalytics({"febre": {}})

    primeira = LanguageClient(fake, azure).analyze_health(["Estou com febre."])
    segunda_cliente = LanguageClient(fake, azure)
    segunda = segunda_cliente.analyze_health(["Estou com febre."])

    assert len(fake.health_calls) == 1
    assert primeira == segunda
    assert segunda_cliente.model_versions["health"] == "2023-04-28"


def test_temporalidade_lida_do_json_cru_quando_o_sdk_nao_expoe(config):
    class FakeComTemporalidade(FakeTextAnalytics):
        def begin_analyze_healthcare_entities(self, documents, *, language, model_version, raw_response_hook=None):
            poller = super().begin_analyze_healthcare_entities(
                documents, language=language, model_version=model_version)
            raw_response_hook(SimpleNamespace(raw_response={"status": "succeeded", "tasks": {"items": [{"results": {
                "modelVersion": "2099-01-01",
                "documents": [{"id": "0", "entities": [{"offset": 5, "length": 9, "assertion": {"temporality": "past"}}]}],
            }}]}}))
            return poller

    cliente = LanguageClient(FakeComTemporalidade({"convulsão": {}}), config.azure)
    entidade, = cliente.analyze_health(["Tive convulsão."])[0]
    assert entidade.temporality == "past"
    assert cliente.model_versions["health"] == "2099-01-01"


@pytest.mark.parametrize("valor, esperado", [
    ("negativePossible", "negative_possible"),
    ("negative", "negative"),
    (SimpleNamespace(value="hypothetical"), "hypothetical"),
    (None, None),
])
def test_valores_de_assercao_viram_snake_case(valor, esperado):
    assert snake(valor) == esperado
