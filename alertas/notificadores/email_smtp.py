"""Canal de e-mail por SMTP (Gmail com senha de app, Brevo etc.).

``smtplib`` é síncrono, então o envio roda em ``asyncio.to_thread`` para não
bloquear o event loop do FastAPI.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from ..modelos import Alerta
from . import formatacao
from .base import LigacaoAck


class NotificadorEmail:
    canal = "email"

    def __init__(
        self,
        *,
        servidor: str,
        porta: int,
        remetente: str,
        destinatarios: tuple[str, ...],
        usuario: str | None = None,
        senha: str | None = None,
        destinatarios_simulado: tuple[str, ...] = (),
        starttls: bool = True,
        ligacao_ack: LigacaoAck | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._servidor = servidor
        self._porta = porta
        self._remetente = remetente
        self._destinatarios = destinatarios
        self._destinatarios_simulado = destinatarios_simulado
        self._usuario = usuario
        self._senha = senha
        self._starttls = starttls
        self._ligacao_ack = ligacao_ack
        self._timeout = timeout

    async def enviar(self, alerta: Alerta) -> None:
        destinos = self._destinatarios_para(alerta)
        if not destinos:
            raise RuntimeError("nenhum destinatário de e-mail configurado")
        mensagem = self.montar(alerta, destinos)
        await asyncio.to_thread(self._entregar, mensagem)

    def montar(self, alerta: Alerta, destinos: tuple[str, ...]) -> EmailMessage:
        mensagem = EmailMessage()
        mensagem["Subject"] = formatacao.assunto(alerta)
        mensagem["From"] = self._remetente
        mensagem["To"] = ", ".join(destinos)
        mensagem.set_content(formatacao.corpo_texto(alerta, self._ligacao_ack))
        mensagem.add_alternative(formatacao.corpo_html(alerta, self._ligacao_ack), subtype="html")
        return mensagem

    def _destinatarios_para(self, alerta: Alerta) -> tuple[str, ...]:
        if alerta.is_synthetic and self._destinatarios_simulado:
            return self._destinatarios_simulado
        return self._destinatarios

    def _entregar(self, mensagem: EmailMessage) -> None:
        with smtplib.SMTP(self._servidor, self._porta, timeout=self._timeout) as smtp:
            if self._starttls:
                smtp.starttls()
            if self._usuario:
                smtp.login(self._usuario, self._senha or "")
            smtp.send_message(mensagem)
