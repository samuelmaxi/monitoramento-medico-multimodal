"""Casamento das entidades do serviço com a lista de termos críticos (``critical_terms.yaml``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import CriticalTerm, CriticalTerms
from .language_client import HealthEntity
from .normalize import contains_phrase, normalize


@dataclass(frozen=True)
class TermMatch:
    term: CriticalTerm
    via: str  # "nome_normalizado" ou "sinonimo:<sinônimo>"


class TermMatcher:
    def __init__(self, termos: CriticalTerms) -> None:
        self._termos = [
            (
                t,
                frozenset(normalize(n) for n in t.normalized),
                # Sinônimo mais longo primeiro: "dor no peito" antes de "dor".
                sorted({normalize(s) for s in (t.name, *t.synonyms)}, key=len, reverse=True),
            )
            for t in termos.terms
        ]

    def match(self, entidade: HealthEntity) -> Optional[TermMatch]:
        """Primeiro termo cuja categoria aceita a entidade e cujo nome ou sinônimo casa."""
        normalizado = normalize(entidade.normalized_text or "")
        texto = normalize(entidade.text)
        for termo, nomes_normalizados, sinonimos in self._termos:
            if entidade.category not in termo.categories:
                continue
            if normalizado and normalizado in nomes_normalizados:
                return TermMatch(termo, "nome_normalizado")
            for sinonimo in sinonimos:
                if contains_phrase(texto, sinonimo):
                    return TermMatch(termo, f"sinonimo:{sinonimo}")
        return None
