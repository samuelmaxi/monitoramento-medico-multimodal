"""Configuração do motor de alertas, lida das variáveis ``ALERTA_*`` e ``SMTP_*``."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .regras import CAMINHO_REGRAS

VERDADEIROS = {"1", "true", "sim", "yes", "on"}


@dataclass(frozen=True)
class ConfigAlertas:
    regras_path: Path = CAMINHO_REGRAS
    armazenamento: str = "jsonl"  # jsonl | memoria | cosmos (cosmos: fase 5)
    saida_dir: Path = Path("saida")
    timer_s: float = 30.0
    tentativas_max: int = 3
    destinatarios: tuple[str, ...] = ()
    destinatarios_simulado: tuple[str, ...] = ()
    smtp_servidor: str | None = None
    smtp_porta: int = 587
    smtp_usuario: str | None = None
    smtp_senha: str | None = None
    smtp_remetente: str | None = None
    smtp_starttls: bool = True
    webhook_url: str | None = None
    base_url: str = "http://localhost:8000"
    ack_secret: str | None = None

    @property
    def email_configurado(self) -> bool:
        return bool(self.smtp_servidor and self.smtp_remetente and self.destinatarios)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ConfigAlertas:
        e = os.environ if env is None else env
        return cls(
            regras_path=Path(e.get("ALERTA_REGRAS_PATH") or CAMINHO_REGRAS),
            armazenamento=(e.get("ALERTA_STORE") or "jsonl").strip().lower(),
            saida_dir=Path(e.get("ALERTA_SAIDA_DIR") or "saida"),
            timer_s=float(e.get("ALERTA_TIMER_S") or 30),
            tentativas_max=int(e.get("ALERTA_TENTATIVAS_MAX") or 3),
            destinatarios=_lista(e.get("ALERTA_DESTINATARIOS")),
            destinatarios_simulado=_lista(e.get("ALERTA_DESTINATARIOS_SIMULADO")),
            smtp_servidor=e.get("SMTP_HOST") or None,
            smtp_porta=int(e.get("SMTP_PORT") or 587),
            smtp_usuario=e.get("SMTP_USUARIO") or None,
            smtp_senha=e.get("SMTP_SENHA") or None,
            smtp_remetente=e.get("SMTP_REMETENTE") or None,
            smtp_starttls=(e.get("SMTP_STARTTLS") or "true").strip().lower() in VERDADEIROS,
            webhook_url=e.get("ALERTA_WEBHOOK_URL") or None,
            base_url=(e.get("ALERTA_PUBLIC_BASE_URL") or "http://localhost:8000").rstrip("/"),
            ack_secret=e.get("ALERTA_ACK_SECRET") or None,
        )


def _lista(valor: str | None) -> tuple[str, ...]:
    return tuple(item.strip() for item in (valor or "").split(",") if item.strip())
