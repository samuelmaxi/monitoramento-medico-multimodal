"""Interface de canal de notificação. O motor não sabe qual canal existe, só o nome e ``enviar``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ..modelos import Alerta

LigacaoAck = Callable[[Alerta], str]


class Notifier(Protocol):
    canal: str

    async def enviar(self, alerta: Alerta) -> None:
        """Entrega o alerta. Levanta exceção se o canal falhar."""
