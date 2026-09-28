from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from contratos import EventoAchado, pseudonimizar_paciente

from .constantes import CHAVE_TESTE, DIR_EXEMPLOS


@pytest.fixture(autouse=True)
def _chave(monkeypatch):
    monkeypatch.setenv("PSEUDONYM_KEY", CHAVE_TESTE)


@pytest.fixture
def paciente() -> str:
    return pseudonimizar_paciente(10000032, chave=CHAVE_TESTE)


@pytest.fixture
def instante() -> datetime:
    return datetime(2026, 9, 26, 17, 2, tzinfo=timezone.utc)


@pytest.fixture
def campos_validos(paciente, instante) -> dict:
    """Evento mínimo válido. Os testes alteram um campo por vez."""
    return {
        "event_id": str(uuid.uuid4()),
        "patient_id": paciente,
        "modality": "sinais_vitais",
        "event_type": "news2_emergencia",
        "timestamp": instante.isoformat().replace("+00:00", "Z"),
        "detected_at": (instante + timedelta(seconds=3)).isoformat().replace("+00:00", "Z"),
        "score": 0.9,
        "severity": "alta",
        "evidence": {"summary": "NEWS2 = 8", "features": {"news2_total": 8}},
        "model_version": "anomalias-sinais@0.1.0",
    }


@pytest.fixture
def evento(campos_validos) -> EventoAchado:
    return EventoAchado.model_validate(campos_validos)


@pytest.fixture
def exemplos() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(DIR_EXEMPLOS.glob("*.json"))}

