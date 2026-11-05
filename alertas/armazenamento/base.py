"""Interface do armazenamento de alertas. O motor e o despachante só conhecem este Protocol.

Trocar JSONL por Cosmos DB é trocar a implementação, sem mexer na lógica.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from ..modelos import Alerta, EventoDeAlerta, StatusAlerta, TipoAuditoria, novo_evento_de_alerta


class AlertStore(Protocol):
    def salvar(self, alerta: Alerta) -> None: ...

    def obter(self, alerta_id: uuid.UUID) -> Alerta | None: ...

    def buscar_aberto(self, chave: str) -> Alerta | None:
        """Alerta mais recente com a chave que ainda não foi reconhecido."""

    def abertos(self) -> list[Alerta]: ...

    def pendentes_de_envio(self, agora: datetime, tentativas_max: int) -> list[Alerta]:
        """Pendentes, e falhas com tentativas restantes cujo horário de retentativa já chegou."""

    def listar(
        self,
        *,
        status: StatusAlerta | None = None,
        prioridade: str | None = None,
        paciente_id: str | None = None,
        limite: int = 100,
    ) -> list[Alerta]: ...

    def registrar_evento(self, evento: EventoDeAlerta) -> None: ...

    def eventos(self, alerta_id: uuid.UUID) -> list[EventoDeAlerta]: ...


def gravar(
    armazenamento: AlertStore,
    alerta: Alerta,
    tipo: TipoAuditoria,
    agora: datetime,
    detalhe: str = "",
) -> None:
    """Grava o estado do alerta e a linha de auditoria correspondente."""
    armazenamento.salvar(alerta)
    armazenamento.registrar_evento(novo_evento_de_alerta(alerta, tipo, agora, detalhe))
