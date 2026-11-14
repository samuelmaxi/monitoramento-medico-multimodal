"""Garantia de que o relatório não carrega dado pessoal (DoD da US09).

O relatório identifica o vídeo só pelo ``source_id`` e pelo SHA-256 do arquivo. Nunca entram o
nome do arquivo, o caminho (``C:\\Users\\fulano\\...`` já é um nome), o ID do paciente nem o
leito. Esta etapa é a rede de segurança: varre todo texto do relatório e troca por um marcador
o que parecer CPF, e-mail ou caminho de arquivo. Não substitui revisão humana, como o guarda-corpo
equivalente do contrato de eventos (``contratos.evento``).
"""

from __future__ import annotations

import re
from typing import Any

_PADROES = (
    (re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"), "[CPF removido]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[e-mail removido]"),
    # Windows (C:\pasta\arquivo ou C:/pasta/arquivo) e POSIX (/home/..., /Users/...).
    (re.compile(r"\b[A-Za-z]:[\\/][^\s\"'<>|]+"), "[caminho removido]"),
    (
        re.compile(r"(?<![\w.])/(?:Users|home|mnt|var|tmp|private)/[^\s\"'<>|]+"),
        "[caminho removido]",
    ),
)


def limpar_texto(texto: str) -> tuple[str, int]:
    """Texto sem os padrões de dado pessoal e quantas trocas foram feitas."""
    trocas = 0
    for padrao, marcador in _PADROES:
        texto, n = padrao.subn(marcador, texto)
        trocas += n
    return texto, trocas


def limpar(valor: Any) -> tuple[Any, int]:
    """Aplica :func:`limpar_texto` a todo texto de dicts, listas e tuplas (chaves ficam)."""
    if isinstance(valor, str):
        return limpar_texto(valor)
    if isinstance(valor, dict):
        total = 0
        saida = {}
        for chave, item in valor.items():
            saida[chave], n = limpar(item)
            total += n
        return saida, total
    if isinstance(valor, (list, tuple)):
        total = 0
        itens = []
        for item in valor:
            limpo, n = limpar(item)
            itens.append(limpo)
            total += n
        return itens, total
    return valor, 0


__all__ = ["limpar", "limpar_texto"]
