"""Pseudonimização de identificadores de paciente e internação.

Usa HMAC-SHA256 com chave secreta: o mesmo identificador sempre gera o mesmo
pseudônimo (a fusão consegue cruzar modalidades), mas sem a chave não é
possível voltar ao identificador original nem montar uma tabela de ataque por
força bruta, como seria com um hash puro de IDs sequenciais.

A chave vem da variável de ambiente ``PSEUDONYM_KEY``, definida no ``.env``
de cada integrante (nunca versionada). Todos do grupo usam a mesma chave, senão
o mesmo paciente teria pseudônimos diferentes em cada máquina.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re

VAR_AMBIENTE_CHAVE = "PSEUDONYM_KEY"
TAMANHO_MIN_CHAVE_BYTES = 32
TAMANHO_DIGEST_HEX = 24  # 96 bits: colisão desprezível no volume do projeto

PREFIXO_PACIENTE = "pt"
PREFIXO_INTERNACAO = "enc"

PADRAO_PACIENTE = rf"^{PREFIXO_PACIENTE}_[0-9a-f]{{{TAMANHO_DIGEST_HEX}}}$"
PADRAO_INTERNACAO = rf"^{PREFIXO_INTERNACAO}_[0-9a-f]{{{TAMANHO_DIGEST_HEX}}}$"

_RE_PACIENTE = re.compile(PADRAO_PACIENTE)


class ChavePseudonimizacaoAusente(RuntimeError):
    """A chave não foi configurada ou é curta demais."""


def _carregar_chave(chave: bytes | str | None) -> bytes:
    if chave is None:
        chave = os.environ.get(VAR_AMBIENTE_CHAVE)
    if not chave:
        raise ChavePseudonimizacaoAusente(
            f"Defina {VAR_AMBIENTE_CHAVE} (Secrets Manager na AWS, .env localmente)."
        )
    chave_bytes = chave.encode("utf-8") if isinstance(chave, str) else chave
    if len(chave_bytes) < TAMANHO_MIN_CHAVE_BYTES:
        raise ChavePseudonimizacaoAusente(
            f"{VAR_AMBIENTE_CHAVE} precisa ter ao menos {TAMANHO_MIN_CHAVE_BYTES} bytes. "
            "Gere uma com: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    return chave_bytes


def _pseudonimo(prefixo: str, identificador: str | int, chave: bytes | str | None) -> str:
    valor = str(identificador).strip()
    if not valor:
        raise ValueError("identificador vazio")
    mensagem = f"{prefixo}:{valor}".encode("utf-8")
    digest = hmac.new(_carregar_chave(chave), mensagem, hashlib.sha256).hexdigest()
    return f"{prefixo}_{digest[:TAMANHO_DIGEST_HEX]}"


def pseudonimizar_paciente(identificador: str | int, *, chave: bytes | str | None = None) -> str:
    """``subject_id`` (MIMIC) ou outro ID de origem → ``pt_<24 hex>``."""
    return _pseudonimo(PREFIXO_PACIENTE, identificador, chave)


def pseudonimizar_internacao(identificador: str | int, *, chave: bytes | str | None = None) -> str:
    """``hadm_id`` (MIMIC) ou outro ID de internação → ``enc_<24 hex>``."""
    return _pseudonimo(PREFIXO_INTERNACAO, identificador, chave)


def e_pseudonimo_de_paciente(valor: str) -> bool:
    return bool(_RE_PACIENTE.fullmatch(valor))
