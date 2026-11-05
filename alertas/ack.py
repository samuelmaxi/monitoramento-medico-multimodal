"""Token do link de reconhecimento enviado no e-mail (``/alerts/{id}/ack?token=``).

HMAC do identificador do alerta com um segredo do servidor. Sem o segredo, ninguém
reconhece alertas alheios apenas adivinhando um UUID.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid


def gerar_token(alerta_id: uuid.UUID, segredo: str) -> str:
    mensagem = str(alerta_id).encode("ascii")
    return hmac.new(segredo.encode("utf-8"), mensagem, hashlib.sha256).hexdigest()


def verificar_token(alerta_id: uuid.UUID, token: str, segredo: str) -> bool:
    return hmac.compare_digest(gerar_token(alerta_id, segredo), token)
