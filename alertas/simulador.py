"""Achados sintéticos (``is_synthetic=True``) de qualquer modalidade do catálogo.

Permite testar o motor e a entrega antes de os detectores estarem prontos. Uma
modalidade sem detector real só precisa publicar ``EventoAchado`` no barramento:
o motor não distingue o simulado do real.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta

from contratos import (
    SEM_ACHADOS,
    TIPOS_DE_EVENTO,
    EventoAchado,
    Modalidade,
    Severidade,
    agora_utc,
    pseudonimizar_paciente,
)

MODEL_VERSION = "simulador-alertas@0.1.0"
PACIENTE_SIMULADO = 10000001
SCORE_POR_SEVERIDADE = {
    Severidade.INFO: 0.1,
    Severidade.BAIXA: 0.3,
    Severidade.MEDIA: 0.6,
    Severidade.ALTA: 0.9,
}


def evento_sintetico(
    modalidade: Modalidade | str,
    tipo: str,
    *,
    severidade: Severidade | str = Severidade.MEDIA,
    paciente_id: str | None = None,
    leito: str | None = "UTI-01",
    com_ingestao: bool = True,
    agora: datetime | None = None,
    resumo: str | None = None,
    features: dict | None = None,
    thresholds: dict | None = None,
) -> EventoAchado:
    modalidade = Modalidade(modalidade)
    severidade = Severidade(severidade)
    instante = agora or agora_utc()
    campos: dict = {
        "patient_id": paciente_id or pseudonimizar_paciente(PACIENTE_SIMULADO),
        "modality": modalidade,
        "event_type": tipo,
        "timestamp": instante,
        "detected_at": instante + timedelta(seconds=1),
        "ingested_at": instante if com_ingestao else None,
        "score": SCORE_POR_SEVERIDADE[severidade],
        "severity": severidade,
        "evidence": {
            "summary": resumo or f"[simulado] {tipo.replace('_', ' ')} em {modalidade.value}",
            "features": features or {},
            "thresholds": thresholds or {},
        },
        "model_version": MODEL_VERSION,
        "is_synthetic": True,
    }
    if leito is not None:
        campos["context"] = {"bed_id": leito, "source_id": "simulador"}
    if modalidade is Modalidade.FUSAO:
        campos["related_event_ids"] = [uuid.uuid4()]
    return EventoAchado.criar(**campos)


def cenario_news2_emergencia(agora: datetime | None = None) -> EventoAchado:
    """Cenário do DoD da US19: SpO2 caindo e NEWS2 = 8, que deve gerar um alerta alta."""
    return evento_sintetico(
        Modalidade.SINAIS_VITAIS,
        "news2_emergencia",
        severidade=Severidade.ALTA,
        agora=agora,
        resumo="NEWS2 = 8 (SpO2 89%, FC 128 bpm)",
        features={"spo2": 89, "frequencia_cardiaca": 128, "news2_total": 8},
        thresholds={"news2_emergencia": 7},
    )


def todos_os_tipos(
    severidade: Severidade | str = Severidade.MEDIA, agora: datetime | None = None
) -> Iterator[EventoAchado]:
    """Um achado por tipo do catálogo (sem ``sem_achados``), de todas as modalidades."""
    for modalidade, tipos in TIPOS_DE_EVENTO.items():
        for tipo in tipos:
            if tipo != SEM_ACHADOS:
                yield evento_sintetico(modalidade, tipo, severidade=severidade, agora=agora)
