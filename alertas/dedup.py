"""Deduplicação e escalonamento por persistência. Funções puras sobre o estado do alerta."""

from __future__ import annotations

from datetime import datetime

from contratos import EventoAchado, Severidade

from .modelos import Alerta, StatusAlerta
from .regras import Politica

ORDEM_PRIORIDADE = (Severidade.BAIXA, Severidade.MEDIA, Severidade.ALTA)


def chave_dedup(evento: EventoAchado) -> str:
    return f"{evento.patient_id}|{evento.modality.value}|{evento.event_type}"


def proxima_prioridade(prioridade: Severidade) -> Severidade:
    indice = ORDEM_PRIORIDADE.index(prioridade)
    return ORDEM_PRIORIDADE[min(indice + 1, len(ORDEM_PRIORIDADE) - 1)]


def motivo_escalonamento(alerta: Alerta, politica: Politica, agora: datetime) -> str | None:
    """Razão para subir a prioridade agora, ou ``None``. Alerta reconhecido ou alta não escalona."""
    if alerta.status is StatusAlerta.RECONHECIDO or alerta.priority is Severidade.ALTA:
        return None
    if alerta.occurrences_since_escalation >= politica.escalar_apos_ocorrencias:
        return "ocorrências"
    if agora - alerta.escalation_mark_at >= politica.escalar_apos:
        return "tempo"
    return None
