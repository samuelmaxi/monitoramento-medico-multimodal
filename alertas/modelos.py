"""Modelo do alerta e da trilha de auditoria (US19).

``Alerta`` é o estado atual de um alerta. Cada mudança é gravada como uma nova
versão no armazenamento. ``EventoDeAlerta`` é a trilha append-only: criado,
suprimido, escalonado, enviado, falhou, registrado e reconhecido.

Os campos seguem o contrato em inglês (``patient_id``, ``severity``) e os valores
de prioridade vêm do catálogo do contrato (``alta``, ``media``, ``baixa``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from contratos import Severidade


class StatusAlerta(str, Enum):
    PENDENTE = "pending"       # aguarda envio (recém-criado, escalonado ou recuperado)
    ENVIADO = "sent"           # todos os canais configurados receberam
    FALHOU = "failed"          # algum canal falhou; retenta até o limite
    REGISTRADO = "registered"  # prioridade sem canal de envio (ex.: baixa): só fica no painel
    RECONHECIDO = "ack"        # equipe confirmou; encerra o alerta


class TipoAuditoria(str, Enum):
    CRIADO = "created"
    SUPRIMIDO = "suppressed"
    ESCALONADO = "escalated"
    ENVIADO = "sent"
    FALHOU = "failed"
    REGISTRADO = "registered"
    RECONHECIDO = "acked"


class Alerta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    dedup_key: str = Field(description="patient_id|modality|event_type")
    patient_id: str
    status: StatusAlerta
    priority: Severidade
    modalities: list[str]
    event_type: str
    bed_id: str | None = None
    summary: str
    suggested_action: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    rule_origin: str = Field(description="regra:<modalidade>/<tipo> ou padrao:<severidade>")
    is_synthetic: bool = False

    occurrences: int = 1
    occurrences_since_escalation: int = 1
    escalation_level: int = 0
    escalation_mark_at: datetime = Field(description="Início da contagem de persistência")
    last_seen_at: datetime
    source_event_ids: list[uuid.UUID] = Field(default_factory=list)

    event_at: datetime = Field(description="Quando o fenômeno ocorreu (timestamp do achado)")
    ingested_at: datetime | None = None
    detected_at: datetime
    alert_created_at: datetime
    sent_at: datetime | None = Field(default=None, description="Primeiro envio bem-sucedido")
    latency_base: str = Field(description="ingested_at, ou detected_at sem ingestão")

    pending_channels: list[str] = Field(default_factory=list)
    retries: int = 0
    next_attempt_at: datetime | None = None
    acked_at: datetime | None = None
    acked_by: str | None = None


class EventoDeAlerta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    alert_id: uuid.UUID
    patient_id: str
    type: TipoAuditoria
    at: datetime
    detail: str = ""


def novo_evento_de_alerta(
    alerta: Alerta, tipo: TipoAuditoria, agora: datetime, detalhe: str = ""
) -> EventoDeAlerta:
    return EventoDeAlerta(
        id=uuid.uuid4(),
        alert_id=alerta.id,
        patient_id=alerta.patient_id,
        type=tipo,
        at=agora,
        detail=detalhe,
    )
