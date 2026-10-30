"""Serviço de alertas: junta motor, despachante e timer, e liga o motor ao barramento."""

from __future__ import annotations

import asyncio
import logging

from contratos import BarramentoLocal, Severidade, filtro

from .armazenamento import AlertStore
from .despachante import Despachante
from .motor import MotorAlertas

log = logging.getLogger(__name__)

NOME_ASSINANTE = "alertas-motor"


class ServicoAlertas:
    def __init__(
        self,
        motor: MotorAlertas,
        despachante: Despachante,
        *,
        segredo_ack: str,
        intervalo_timer_s: float = 30.0,
    ) -> None:
        self.motor = motor
        self.despachante = despachante
        self.segredo_ack = segredo_ack
        self._intervalo = intervalo_timer_s

    @property
    def armazenamento(self) -> AlertStore:
        return self.motor.armazenamento

    def assinar(self, barramento: BarramentoLocal) -> None:
        """Recebe todo achado de severidade baixa ou acima. O ``info`` nunca vira alerta."""
        barramento.assinar(
            NOME_ASSINANTE, self.motor.receber, filtro(severidade_minima=Severidade.BAIXA)
        )

    async def executar(self) -> None:
        """Worker e timer. Roda até ser cancelado (no ``lifespan`` do FastAPI)."""
        await asyncio.gather(self.despachante.executar(), self._laco_timer())

    async def processar_pendentes(self) -> None:
        """Sem worker em segundo plano (CLI e testes): recupera pendências e envia tudo."""
        self.despachante.reenfileirar_devidos()
        await self.despachante.processar_fila()

    async def _laco_timer(self) -> None:
        while True:
            await asyncio.sleep(self._intervalo)
            self.motor.varrer()
            self.despachante.reenfileirar_devidos()
