"""Precisão e recall por evento contra vídeos rotulados (normal vs. desvio).

A unidade rotulada é um trecho de vídeo com rótulo binário: uma repetição do
REHAB24-6 (``correctness`` 0 = desvio) ou um clipe inteiro do FallVision
(pasta ``Fall`` = desvio).

- Cada evento é atribuído à unidade com que mais se sobrepõe no tempo. Evento
  em unidade com desvio é acerto; em unidade normal, ou fora de qualquer
  unidade (entre repetições), é falso positivo.
- Uma unidade com desvio é detectada se recebeu pelo menos um evento.

    precisão = eventos em unidade com desvio / eventos
    recall   = unidades com desvio detectadas / unidades com desvio

Só contam eventos dentro do intervalo coberto pelos rótulos (do início da
primeira unidade ao fim da última): o resto do vídeo não tem gabarito.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .modelos import Desvio


@dataclass(frozen=True)
class Rotulo:
    unidade: str
    inicio_s: float
    fim_s: float
    desvio: bool


@dataclass
class Contagem:
    eventos: int = 0
    eventos_corretos: int = 0
    unidades_desvio: int = 0
    unidades_desvio_detectadas: int = 0
    unidades_normais: int = 0
    unidades_normais_com_evento: int = 0
    perdidas: list[str] = field(default_factory=list)
    """Unidades com desvio sem nenhum evento (falsos negativos)."""
    alarmes_falsos: list[str] = field(default_factory=list)
    """Unidades normais com evento, ou ``fora@<t>s`` para evento entre unidades."""

    def __iadd__(self, outra: Contagem) -> Contagem:
        self.eventos += outra.eventos
        self.eventos_corretos += outra.eventos_corretos
        self.unidades_desvio += outra.unidades_desvio
        self.unidades_desvio_detectadas += outra.unidades_desvio_detectadas
        self.unidades_normais += outra.unidades_normais
        self.unidades_normais_com_evento += outra.unidades_normais_com_evento
        self.perdidas += outra.perdidas
        self.alarmes_falsos += outra.alarmes_falsos
        return self

    @property
    def precisao(self) -> float | None:
        return self.eventos_corretos / self.eventos if self.eventos else None

    @property
    def recall(self) -> float | None:
        return (
            self.unidades_desvio_detectadas / self.unidades_desvio if self.unidades_desvio else None
        )

    @property
    def f1(self) -> float | None:
        p, r = self.precisao, self.recall
        if p is None or r is None or p + r == 0:
            return None
        return 2 * p * r / (p + r)

    def para_dict(self) -> dict[str, object]:
        def arred(valor: float | None) -> float | None:
            return None if valor is None else round(valor, 3)

        return {
            "precisao": arred(self.precisao),
            "recall": arred(self.recall),
            "f1": arred(self.f1),
            "eventos": self.eventos,
            "eventos_corretos": self.eventos_corretos,
            "unidades_desvio": self.unidades_desvio,
            "unidades_desvio_detectadas": self.unidades_desvio_detectadas,
            "unidades_normais": self.unidades_normais,
            "unidades_normais_com_evento": self.unidades_normais_com_evento,
            "perdidas": self.perdidas,
            "alarmes_falsos": self.alarmes_falsos,
        }


def _sobreposicao(desvio: Desvio, rotulo: Rotulo) -> float:
    return min(desvio.fim_s, rotulo.fim_s) - max(desvio.inicio_s, rotulo.inicio_s)


def avaliar(
    desvios: Iterable[Desvio],
    rotulos: Sequence[Rotulo],
    *,
    tipos: Iterable[str] | None = None,
) -> Contagem:
    """Compara os desvios de um vídeo com as unidades rotuladas desse vídeo."""
    contagem = Contagem()
    if not rotulos:
        return contagem
    tipos = set(tipos) if tipos is not None else None
    inicio = min(r.inicio_s for r in rotulos)
    fim = max(r.fim_s for r in rotulos)
    com_evento: set[str] = set()
    for desvio in desvios:
        if tipos is not None and desvio.event_type not in tipos:
            continue
        if desvio.fim_s <= inicio or desvio.inicio_s >= fim:
            continue
        contagem.eventos += 1
        melhor = max(rotulos, key=lambda r: _sobreposicao(desvio, r))
        if _sobreposicao(desvio, melhor) <= 0:
            contagem.alarmes_falsos.append(f"fora@{desvio.pico_s:.1f}s")
            continue
        com_evento.add(melhor.unidade)
        if melhor.desvio:
            contagem.eventos_corretos += 1
    for rotulo in rotulos:
        if rotulo.desvio:
            contagem.unidades_desvio += 1
            if rotulo.unidade in com_evento:
                contagem.unidades_desvio_detectadas += 1
            else:
                contagem.perdidas.append(rotulo.unidade)
        else:
            contagem.unidades_normais += 1
            if rotulo.unidade in com_evento:
                contagem.unidades_normais_com_evento += 1
                contagem.alarmes_falsos.append(rotulo.unidade)
    return contagem
