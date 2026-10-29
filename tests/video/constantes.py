"""Constantes dos testes da US07."""

from __future__ import annotations

from pathlib import Path

CHAVE_TESTE = "chave-de-teste-com-mais-de-32-bytes-000000"
"""Mesma chave usada em ``tests/contratos/constantes.py``.

Fixa no repositório de propósito: é chave de teste, nunca usada em produção —
``PSEUDONYM_KEY`` real fica no ``.env`` de cada integrante.
"""

DIR_CONFIG = Path(__file__).resolve().parents[2] / "config"
