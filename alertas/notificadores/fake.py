"""Notificador de teste. Registra o que recebeu e pode falhar um número fixo de vezes."""

from __future__ import annotations

from ..modelos import Alerta


class NotificadorFake:
    def __init__(self, canal: str = "email", falhas: int = 0) -> None:
        self.canal = canal
        self.enviados: list[Alerta] = []
        self.tentativas = 0
        self._falhas_restantes = falhas

    async def enviar(self, alerta: Alerta) -> None:
        self.tentativas += 1
        if self._falhas_restantes > 0:
            self._falhas_restantes -= 1
            raise ConnectionError(f"{self.canal} indisponível (falha simulada)")
        self.enviados.append(alerta.model_copy(deep=True))
