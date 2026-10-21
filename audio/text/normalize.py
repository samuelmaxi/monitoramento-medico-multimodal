"""Normalização de texto para comparar termos: sem caixa, sem acento, espaços simples."""

from __future__ import annotations

import re
import unicodedata

_ESPACOS = re.compile(r"\s+")


def normalize(texto: str) -> str:
    """``"  Dor  TORÁCICA "`` → ``"dor toracica"``."""
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return _ESPACOS.sub(" ", sem_acento.casefold()).strip()


def contains_phrase(texto_normalizado: str, frase_normalizada: str) -> bool:
    """A frase aparece no texto como palavras inteiras (``"dor"`` não casa ``"dormir"``)."""
    if not frase_normalizada:
        return False
    padrao = rf"(?<!\w){re.escape(frase_normalizada)}(?!\w)"
    return re.search(padrao, texto_normalizado) is not None
