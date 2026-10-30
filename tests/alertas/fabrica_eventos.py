"""Atalhos de teste para achados sintéticos, sem repetir os campos do contrato."""

from __future__ import annotations

from datetime import datetime

from alertas import evento_sintetico
from contratos import EventoAchado, Modalidade, Severidade


def evento(
    modalidade: Modalidade | str,
    tipo: str,
    severidade: Severidade | str = Severidade.MEDIA,
    *,
    agora: datetime | None = None,
    **extra,
) -> EventoAchado:
    return evento_sintetico(modalidade, tipo, severidade=severidade, agora=agora, **extra)
