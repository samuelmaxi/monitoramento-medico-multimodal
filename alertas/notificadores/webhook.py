"""Canal de webhook (Teams ou Slack). Envia ``{"text": ...}``, formato aceito pelos dois."""

from __future__ import annotations

import asyncio
import json
import urllib.request

from ..modelos import Alerta
from . import formatacao
from .base import LigacaoAck


class NotificadorWebhook:
    canal = "webhook"

    def __init__(
        self, url: str, *, ligacao_ack: LigacaoAck | None = None, timeout: float = 10.0
    ) -> None:
        self._url = url
        self._ligacao_ack = ligacao_ack
        self._timeout = timeout

    async def enviar(self, alerta: Alerta) -> None:
        texto = formatacao.resumo_curto(alerta, self._ligacao_ack)
        corpo = json.dumps({"text": texto}).encode("utf-8")
        await asyncio.to_thread(self._post, corpo)

    def _post(self, corpo: bytes) -> None:
        requisicao = urllib.request.Request(
            self._url,
            data=corpo,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        # urlopen levanta HTTPError para respostas 4xx e 5xx, então o despachante vê a falha
        with urllib.request.urlopen(requisicao, timeout=self._timeout):
            pass
