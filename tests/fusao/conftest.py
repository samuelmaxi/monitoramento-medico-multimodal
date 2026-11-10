from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from contratos import EventoAchado, Modalidade, Severidade
from fusao import carregar_config

RAIZ = Path(__file__).resolve().parents[2]
CHAVE_TESTE = "chave-de-teste-com-mais-de-32-bytes-000000"
PACIENTE = "pt_" + "a" * 24
OUTRO = "pt_" + "b" * 24
T0 = datetime(2026, 9, 26, 17, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _chave(monkeypatch):
    monkeypatch.setenv("PSEUDONYM_KEY", CHAVE_TESTE)


@pytest.fixture
def config():
    return carregar_config(RAIZ / "config" / "us18_fusao.yaml")


def achado(
    modalidade: Modalidade | str,
    tipo: str,
    severidade: Severidade | str = Severidade.MEDIA,
    *,
    minuto: float = 0,
    score: float = 0.5,
    paciente: str = PACIENTE,
    features: dict | None = None,
    duracao_min: float | None = None,
    leito: str | None = "UTI-07",
    ingestao: bool = True,
    sintetico: bool = False,
) -> EventoAchado:
    """Achado mínimo válido no contrato, ``minuto`` minutos depois de ``T0``."""
    instante = T0 + timedelta(minutes=minuto)
    severidade = Severidade(severidade)
    campos: dict = {
        "patient_id": paciente,
        "modality": Modalidade(modalidade),
        "event_type": tipo,
        "timestamp": instante,
        "ingested_at": instante if ingestao else None,
        "score": 0.0 if tipo == "sem_achados" else score,
        "severity": Severidade.INFO if tipo == "sem_achados" else severidade,
        "evidence": {"summary": f"teste {tipo}", "features": features or {}},
        "model_version": "teste@0.0.1",
        "is_synthetic": sintetico,
    }
    if leito:
        campos["context"] = {"bed_id": leito}
    if duracao_min is not None:
        campos["window"] = {"start": instante, "end": instante + timedelta(minutes=duracao_min)}
    return EventoAchado.criar(**campos)


def instante(minuto: float) -> datetime:
    return T0 + timedelta(minutes=minuto)
