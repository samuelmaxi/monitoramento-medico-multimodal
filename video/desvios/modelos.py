"""Tipos da US08: um desvio detectado e o resultado da análise de um vídeo.

Ainda não são eventos do contrato da US04: :mod:`video.desvios.eventos` faz a
tradução. Assim as regras e a avaliação rodam sem pseudônimo, sem relógio UTC e
sem emissor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from contratos import Severidade


@dataclass(frozen=True)
class Desvio:
    """Um desvio do padrão esperado que persistiu pela janela mínima.

    Tempos em segundos desde o início do vídeo. ``pico_s``/``frame_idx`` apontam
    o quadro de evidência (o de maior desvio no trecho).
    """

    event_type: str
    regra: str
    """Regra do perfil que disparou, ex. ``faixa.inclinacao_tronco`` ou ``queda``."""
    severidade: Severidade
    score: float
    inicio_s: float
    fim_s: float
    pico_s: float
    frame_idx: int | None
    medida: str
    valor: float
    limiar: float
    unidade: str
    janela_s: float
    resumo: str
    features: dict[str, Any] = field(default_factory=dict)
    relacionados: tuple[str, ...] = ()
    """``event_id`` dos eventos da US07 que originaram o desvio."""
    inicio_utc: datetime | None = None
    fim_utc: datetime | None = None
    """Instantes absolutos, quando o desvio vem de eventos que já os têm (US07)."""

    @property
    def duracao_s(self) -> float:
        return self.fim_s - self.inicio_s


@dataclass(frozen=True)
class Repeticao:
    numero: int
    inicio_s: float
    fim_s: float
    pico_s: float
    pico: float
    atingiu_alvo: bool | None
    """None quando o perfil não tem alvo absoluto."""
    frame_pico: int = 0
    medidas: dict[str, float] = field(default_factory=dict)
    """Ângulos na fase de pico, comparados com a referência."""
    referencia: bool = False
    """Faz parte da série de referência (não é julgada)."""


@dataclass(frozen=True)
class ResumoRepeticoes:
    angulo: str
    alvo: float | None
    repeticoes: tuple[Repeticao, ...]
    esperadas: tuple[int, int] | None
    referencia: dict[str, float] | None = None
    """Média dos ângulos das repetições de referência (None sem referência suficiente)."""

    @property
    def contadas(self) -> int:
        return len(self.repeticoes)

    @property
    def dentro_do_esperado(self) -> bool | None:
        if self.esperadas is None:
            return None
        return self.esperadas[0] <= self.contadas <= self.esperadas[1]

    def para_dict(self) -> dict[str, Any]:
        return {
            "angulo": self.angulo,
            "alvo": self.alvo,
            "contadas": self.contadas,
            "esperadas": list(self.esperadas) if self.esperadas else None,
            "dentro_do_esperado": self.dentro_do_esperado,
            "abaixo_do_alvo": sum(r.atingiu_alvo is False for r in self.repeticoes),
            "referencia": self.referencia,
            "repeticoes": [
                {
                    "numero": r.numero,
                    "inicio_s": round(r.inicio_s, 2),
                    "fim_s": round(r.fim_s, 2),
                    "pico": round(r.pico, 1),
                    "pico_s": round(r.pico_s, 2),
                    "atingiu_alvo": r.atingiu_alvo,
                    "referencia": r.referencia,
                    "medidas": r.medidas,
                }
                for r in self.repeticoes
            ],
        }


@dataclass(frozen=True)
class ResultadoAnalise:
    perfil: str
    desvios: tuple[Desvio, ...]
    duracao_s: float | None = None
    quadros: int = 0
    cobertura_pct: float | None = None
    """% de quadros com esqueleto (None quando a análise não usou pose)."""
    confiavel: bool = True
    repeticoes: ResumoRepeticoes | None = None
    avisos: tuple[str, ...] = ()
