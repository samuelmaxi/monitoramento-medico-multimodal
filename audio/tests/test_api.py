"""Testes da rota POST /text/analysis (US13) com o Azure AI Language falso."""

from __future__ import annotations

import json

import pytest
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import text_analysis
from audio.text.language_client import LanguageAuthError, LanguageClient
from contratos import BarramentoLocal

from .conftest import FakeTextAnalytics

REGRAS = {
    "dor no peito": {"normalized": "Chest Pain", "certainty": "positive"},
    "febre": {"normalized": "Fever", "certainty": "negative"},
}


def _transcricao(**sobrescrever):
    dados = {
        "audio_id": "consulta-api",
        "patient_id": "sim-paciente-api",
        "recorded_at": "2026-10-01T13:00:00Z",
        "is_synthetic": True,
        "segments": [
            {"speaker": "medico", "text": "O que o senhor está sentindo?", "offset_ms": 0, "duration_ms": 2000},
            {"speaker": "paciente", "text": "Estou com dor no peito desde ontem. Não tive febre.",
             "offset_ms": 2500, "duration_ms": 4000, "confidence": 0.9},
        ],
    }
    return {**dados, **sobrescrever}


@pytest.fixture
def montar(config, tmp_path, monkeypatch):
    """Cria o TestClient com um SDK falso; devolve (client, fake, caminho do barramento)."""
    barramento_jsonl = tmp_path / "eventos.jsonl"

    def criar(fake: FakeTextAnalytics | None = None, cliente_erro: Exception | None = None):
        fake = fake or FakeTextAnalytics(REGRAS)

        def cliente():
            if cliente_erro is not None:
                raise cliente_erro
            return LanguageClient(fake, config.azure, sleep=lambda _s: None)

        monkeypatch.setattr(text_analysis, "get_text_config", lambda: config)
        monkeypatch.setattr(text_analysis, "get_language_client", cliente)
        monkeypatch.setattr(text_analysis, "get_barramento", lambda: BarramentoLocal(barramento_jsonl))
        app = FastAPI()
        app.include_router(text_analysis.router)
        return TestClient(app), fake, barramento_jsonl

    return criar


def test_termo_afirmado_vira_evento_e_negacao_nao(montar):
    client, fake, barramento = montar()

    resp = client.post("/text/analysis", json=_transcricao())

    assert resp.status_code == 200
    corpo = resp.json()
    termos = [e["evidence"]["features"]["termo"] for e in corpo["eventos"] if e["event_type"] == "termo_critico"]
    assert termos == ["dor torácica"]
    assert corpo["patient_id"].startswith("pt_")
    assert all(e["patient_id"] == corpo["patient_id"] for e in corpo["eventos"])
    assert "detalhes" not in corpo
    # Só a fala do paciente vai para a Azure.
    assert [d["text"] for c in fake.health_calls for d in c["documents"]] == [
        "Estou com dor no peito desde ontem. Não tive febre."
    ]
    publicados = [json.loads(linha) for linha in barramento.read_text(encoding="utf-8").splitlines()]
    assert [e["event_id"] for e in publicados] == [e["event_id"] for e in corpo["eventos"]]


def test_detalhes_trazem_o_descarte_da_negacao(montar):
    client, _, _ = montar()

    resp = client.post("/text/analysis", params={"detalhes": "true"}, json=_transcricao())

    assert resp.status_code == 200
    descartes = resp.json()["detalhes"]["descartes"]
    assert [(d["texto"], d["motivo"]) for d in descartes] == [("febre", "certeza=negative")]


def test_transcricao_invalida_da_422(montar):
    client, fake, _ = montar()

    resp = client.post("/text/analysis", json=_transcricao(recorded_at="2026-10-01T10:00:00-03:00"))

    assert resp.status_code == 422
    assert "UTC" in resp.json()["detail"]
    assert fake.health_calls == []


def test_campo_obrigatorio_ausente_da_422(montar):
    client, _, _ = montar()
    corpo = _transcricao()
    del corpo["segments"]

    assert client.post("/text/analysis", json=corpo).status_code == 422


def test_sem_credencial_da_azure_da_503(montar):
    client, _, _ = montar(cliente_erro=LanguageAuthError("A variável AZURE_LANGUAGE_KEY não foi definida."))

    resp = client.post("/text/analysis", json=_transcricao())

    assert resp.status_code == 503
    assert "AZURE_LANGUAGE_KEY" in resp.json()["detail"]


def test_sem_chave_de_pseudonimizacao_da_503(montar, monkeypatch):
    client, _, _ = montar()
    monkeypatch.delenv("PSEUDONYM_KEY")

    resp = client.post("/text/analysis", json=_transcricao())

    assert resp.status_code == 503
    assert "PSEUDONYM_KEY" in resp.json()["detail"]


def test_azure_recusa_a_chave_da_401(montar):
    client, _, _ = montar(FakeTextAnalytics(REGRAS, falhas=[ClientAuthenticationError(message="Access denied")]))

    assert client.post("/text/analysis", json=_transcricao()).status_code == 401


def test_throttling_persistente_da_429(montar, config):
    erro = HttpResponseError(message="Too Many Requests")
    erro.status_code = 429
    client, _, _ = montar(FakeTextAnalytics(REGRAS, falhas=[erro] * config.azure.retry.max_attempts))

    assert client.post("/text/analysis", json=_transcricao()).status_code == 429


def test_job_do_health_parado_da_502(montar, config):
    client, _, _ = montar(FakeTextAnalytics(REGRAS, jobs_parados=config.azure.retry.max_attempts))

    resp = client.post("/text/analysis", json=_transcricao())

    assert resp.status_code == 502
    assert "health_timeout_s" in resp.json()["detail"]
