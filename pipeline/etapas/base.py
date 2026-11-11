"""Definições de contexto e protocolo para as etapas do pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from contratos import BarramentoLocal, EventoAchado
from pipeline.config import ConfigPipeline
from pipeline.logger import LoggerPipeline


@dataclass
class ContextoPipeline:
    """Estado e dependências compartilhadas entre as etapas."""

    config: ConfigPipeline
    barramento: BarramentoLocal
    logger: LoggerPipeline
    paciente_id: str
    leito_id: str
    eventos_publicados: list[EventoAchado] = field(default_factory=list)
    dados: dict[str, Any] = field(default_factory=dict)


class EtapaPipeline(Protocol):
    """Protocolo que cada etapa do pipeline implementa."""

    nome: str

    def executar(self, contexto: ContextoPipeline) -> list[EventoAchado]:
        ...

