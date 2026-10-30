"""Despachante: envia os alertas pendentes pelos canais, com retentativa e recuperação.

Um único consumidor (``executar``) processa a fila em série. Por isso um alerta
nunca é enviado em paralelo consigo mesmo. ``acordar`` pode ser chamada de qualquer
thread, pois o barramento entrega na thread de quem publicou.

Se o processo cair, o alerta continua ``pending`` no armazenamento e é reenfileirado
na próxima subida. Essa é a garantia de entrega (outbox).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta

from contratos import agora_utc

from .armazenamento import AlertStore, gravar
from .modelos import StatusAlerta, TipoAuditoria
from .notificadores import Notifier

log = logging.getLogger(__name__)


class Despachante:
    def __init__(
        self,
        notificadores: Mapping[str, Notifier],
        armazenamento: AlertStore,
        *,
        relogio: Callable[[], datetime] = agora_utc,
        tentativas_max: int = 3,
        backoff_base_s: float = 5.0,
        backoff_max_s: float = 300.0,
    ) -> None:
        self._notificadores = dict(notificadores)
        self._armazenamento = armazenamento
        self._relogio = relogio
        self._tentativas_max = tentativas_max
        self._backoff_base = timedelta(seconds=backoff_base_s)
        self._backoff_max = timedelta(seconds=backoff_max_s)
        self._fila: deque[uuid.UUID] = deque()
        self._trava = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sinal: asyncio.Event | None = None

    def acordar(self, alerta_id: uuid.UUID) -> None:
        """Enfileira um alerta. Thread-safe. Sem worker, fica na fila até ``processar_fila``."""
        with self._trava:
            self._fila.append(alerta_id)
            loop, sinal = self._loop, self._sinal
        if loop is not None and sinal is not None:
            try:
                loop.call_soon_threadsafe(sinal.set)
            except RuntimeError:
                log.warning("loop encerrado; alerta %s fica pendente até a subida", alerta_id)

    def reenfileirar_devidos(self) -> int:
        """Põe na fila os pendentes e as falhas cujo horário de retentativa já chegou."""
        agora = self._relogio()
        ids = [a.id for a in self._armazenamento.pendentes_de_envio(agora, self._tentativas_max)]
        for alerta_id in ids:
            self.acordar(alerta_id)
        return len(ids)

    async def executar(self) -> None:
        """Laço do worker. Roda até ser cancelado. Recupera pendências ao subir."""
        with self._trava:
            self._loop = asyncio.get_running_loop()
            self._sinal = asyncio.Event()
        try:
            self.reenfileirar_devidos()
            while True:
                # Limpa o sinal antes de drenar a fila: um acordar que chegue depois
                # da drenagem marca o sinal de novo e o laço não dorme sobre trabalho.
                self._sinal.clear()
                await self.processar_fila()
                await self._sinal.wait()
        finally:
            with self._trava:
                self._loop = None
                self._sinal = None

    async def processar_fila(self) -> None:
        """Processa até a fila esvaziar. Usado pelo laço e direto em CLI e testes."""
        while True:
            with self._trava:
                if not self._fila:
                    return
                alerta_id = self._fila.popleft()
            await self._processar(alerta_id)

    async def _processar(self, alerta_id: uuid.UUID) -> None:
        alerta = self._armazenamento.obter(alerta_id)
        if alerta is None or alerta.status not in (StatusAlerta.PENDENTE, StatusAlerta.FALHOU):
            return
        agora = self._relogio()
        adiado = alerta.next_attempt_at is not None and alerta.next_attempt_at > agora
        if alerta.status is StatusAlerta.FALHOU and adiado:
            return

        if not alerta.pending_channels:
            alerta.status = StatusAlerta.REGISTRADO
            gravar(self._armazenamento, alerta, TipoAuditoria.REGISTRADO, agora,
                   "prioridade sem canal de envio")
            return

        enviados: list[str] = []
        falhas: dict[str, str] = {}
        for canal in alerta.pending_channels:
            notificador = self._notificadores.get(canal)
            if notificador is None:
                falhas[canal] = "canal não configurado"
                continue
            try:
                await notificador.enviar(alerta)
            except Exception as exc:
                log.warning("canal %s falhou no alerta %s: %s", canal, alerta.id, exc)
                falhas[canal] = repr(exc)
            else:
                enviados.append(canal)

        agora = self._relogio()
        alerta.pending_channels = [c for c in alerta.pending_channels if c not in enviados]
        if enviados and alerta.sent_at is None:
            alerta.sent_at = agora

        if not alerta.pending_channels:
            alerta.status = StatusAlerta.ENVIADO
            alerta.next_attempt_at = None
            gravar(self._armazenamento, alerta, TipoAuditoria.ENVIADO, agora,
                   f"canais: {', '.join(enviados)}")
            return

        alerta.status = StatusAlerta.FALHOU
        alerta.retries += 1
        alerta.next_attempt_at = (
            agora + self._atraso(alerta.retries) if alerta.retries < self._tentativas_max else None
        )
        detalhe = "; ".join(f"{canal}: {motivo}" for canal, motivo in falhas.items())
        gravar(self._armazenamento, alerta, TipoAuditoria.FALHOU, agora,
               f"{detalhe} (tentativa {alerta.retries} de {self._tentativas_max})")

    def _atraso(self, tentativa: int) -> timedelta:
        """Espera exponencial: 5 s, 10 s, 20 s… limitada a ``backoff_max``."""
        return min(self._backoff_base * (2 ** (tentativa - 1)), self._backoff_max)
