"""Monta o serviço de alertas a partir da configuração. Único módulo que escolhe implementações."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from datetime import datetime

from contratos import agora_utc

from .ack import gerar_token
from .armazenamento import AlertStore, ArmazenamentoJsonl, MemoriaStore
from .config import ConfigAlertas
from .despachante import Despachante
from .motor import MotorAlertas
from .notificadores import NotificadorEmail, NotificadorWebhook, Notifier
from .regras import carregar_regras
from .servico import ServicoAlertas

log = logging.getLogger(__name__)


def construir(
    config: ConfigAlertas, *, relogio: Callable[[], datetime] = agora_utc
) -> ServicoAlertas:
    segredo = config.ack_secret or _segredo_efemero()
    armazenamento = _armazenamento(config)
    notificadores = _notificadores(config, segredo)
    regras = carregar_regras(config.regras_path)

    despachante = Despachante(
        {n.canal: n for n in notificadores},
        armazenamento,
        relogio=relogio,
        tentativas_max=config.tentativas_max,
    )
    motor = MotorAlertas(armazenamento, regras, despachante.acordar, relogio=relogio)
    return ServicoAlertas(
        motor,
        despachante,
        segredo_ack=segredo,
        intervalo_timer_s=config.timer_s,
    )


def _armazenamento(config: ConfigAlertas) -> AlertStore:
    if config.armazenamento == "jsonl":
        return ArmazenamentoJsonl(config.saida_dir)
    if config.armazenamento == "memoria":
        return MemoriaStore()
    raise ValueError(
        f"ALERTA_STORE={config.armazenamento!r} não suportado; use 'jsonl' ou 'memoria' "
        "(Cosmos DB entra na fase 5 da US19)"
    )


def _notificadores(config: ConfigAlertas, segredo: str) -> list[Notifier]:
    def ligacao(alerta) -> str:
        return f"{config.base_url}/alerts/{alerta.id}/ack?token={gerar_token(alerta.id, segredo)}"

    notificadores: list[Notifier] = []
    if config.email_configurado:
        notificadores.append(NotificadorEmail(
            servidor=config.smtp_servidor,
            porta=config.smtp_porta,
            remetente=config.smtp_remetente,
            destinatarios=config.destinatarios,
            usuario=config.smtp_usuario,
            senha=config.smtp_senha,
            destinatarios_simulado=config.destinatarios_simulado,
            starttls=config.smtp_starttls,
            ligacao_ack=ligacao,
        ))
    else:
        log.warning("e-mail desativado: defina SMTP_HOST, SMTP_REMETENTE e ALERTA_DESTINATARIOS")
    if config.webhook_url:
        notificadores.append(NotificadorWebhook(config.webhook_url, ligacao_ack=ligacao))
    else:
        log.warning("canal webhook desativado: defina ALERTA_WEBHOOK_URL")
    return notificadores


def _segredo_efemero() -> str:
    log.warning("ALERTA_ACK_SECRET não definido: links de reconhecimento só valem nesta execução")
    return secrets.token_urlsafe(32)
