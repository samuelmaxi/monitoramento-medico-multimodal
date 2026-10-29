"""API da US15 (POST /sinais-vitais/news2 e /sinais-vitais/medicoes)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import sinais_vitais
from contratos import EventoAchado
from sinais import DetectorSinais

from ..contratos.constantes import CHAVE_TESTE


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PSEUDONYM_KEY", CHAVE_TESTE)
    monkeypatch.setattr(sinais_vitais, "_detector", DetectorSinais())
    app = FastAPI()
    app.include_router(sinais_vitais.router)
    return TestClient(app)


def test_news2(client):
    resposta = client.post(
        "/sinais-vitais/news2", json={"fc": 115, "spo2": 93, "fr": 22, "temp": 38.5, "pas": 0}
    )
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["total"] == 7 and corpo["gatilho"] == "news2_emergencia"
    assert corpo["artefatos"] == ["pas"]
    assert "pas" in corpo["ausentes"]


def test_medicoes_em_streaming(client):
    medicoes = [
        {
            "patient_id": "10000032",
            "timestamp": f"2026-09-26T{h:02d}:00:00Z",
            "sinal": "fc",
            "valor": v,
        }
        for h, v in enumerate([72, 75, 70, 74, 73, 71, 76, 72])
    ]
    assert (
        client.post("/sinais-vitais/medicoes", json={"medicoes": medicoes}).json()["eventos"] == []
    )

    seguinte = [
        {
            "patient_id": "10000032",
            "timestamp": "2026-09-26T08:00:00Z",
            "sinal": "fc",
            "valor": 135,
        },
        {
            "patient_id": "10000032",
            "timestamp": "2026-09-26T09:00:00Z",
            "sinal": "fc",
            "valor": 138,
        },
        {
            "patient_id": "10000032",
            "timestamp": "2026-09-26T09:00:00Z",
            "sinal": "spo2",
            "valor": 0,
        },
    ]
    corpo = client.post("/sinais-vitais/medicoes", json={"medicoes": seguinte}).json()
    tipos = {EventoAchado.model_validate(e).event_type for e in corpo["eventos"]}
    assert {"news2_resposta_urgente", "desvio_baseline"} <= tipos
    assert corpo["artefatos_descartados"] == 1
    assert all(e["patient_id"].startswith("pt_") for e in corpo["eventos"])


def test_sinal_desconhecido_422(client):
    corpo = {
        "medicoes": [
            {"patient_id": "1", "timestamp": "2026-09-26T00:00:00Z", "sinal": "glicose", "valor": 1}
        ]
    }
    assert client.post("/sinais-vitais/medicoes", json=corpo).status_code == 422
