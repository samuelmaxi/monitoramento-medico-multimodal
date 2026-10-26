"""Modelos de domínio da US07 (detecção de objetos e áreas críticas).

Tipos próprios, independentes dos objetos internos da Ultralytics: o resto do
projeto (ROI, estado, eventos, testes) só conhece estas dataclasses. Assim uma
troca deWeights, de tarefa (detect → segment) ou de biblioteca não atravessa a
aplicação.

Convenções de coordenadas: pixels do frame de origem, ``x`` para a direita e
``y`` para baixo, caixa no formato ``(x1, y1, x2, y2)`` inclusivo nos extremos.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Ponto = tuple[float, float]
"""Coordenada ``(x, y)`` em pixels do frame."""

Poligono = tuple[Ponto, ...]
"""Vértices de uma ROI, na ordem em que formam o polígono."""


@dataclass(frozen=True, slots=True)
class Caixa:
    """Bounding box no formato ``xyxy``, em pixels do frame."""

    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        valores = (self.x1, self.y1, self.x2, self.y2)
        if not all(math.isfinite(v) for v in valores):
            raise ValueError(f"Caixa com coordenada não finita: {valores}")
        if self.x2 < self.x1 or self.y2 < self.y1:
            raise ValueError(
                f"Caixa invertida (x2<x1 ou y2<y1): "
                f"({self.x1}, {self.y1}, {self.x2}, {self.y2})"
            )

    @property
    def largura(self) -> float:
        return self.x2 - self.x1

    @property
    def altura(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return self.largura * self.altura

    @property
    def centro(self) -> Ponto:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    def como_lista(self) -> list[float]:
        return [self.x1, self.y1, self.x2, self.y2]


@dataclass(frozen=True, slots=True)
class Deteccao:
    """Uma detecção de objeto, já normalizada para o domínio.

    ``frame_index`` é 0-based e ``tempo_s`` é o instante do frame em segundos,
    derivado de ``frame_index / fps`` pelo leitor de vídeo. ``track_id`` só
    existe quando o detector roda com rastreamento (ByteTrack); ver
    :mod:`video.detector`.
    """

    class_id: int
    class_name: str
    confianca: float
    caixa: Caixa
    frame_index: int
    tempo_s: float
    track_id: int | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confianca <= 1.0:
            raise ValueError(f"confianca fora de [0, 1]: {self.confianca}")

    @property
    def chave_classe(self) -> tuple[int, str]:
        return (self.class_id, self.class_name)

    @property
    def timestamp_video_s(self) -> float:
        return self.tempo_s


@dataclass(frozen=True, slots=True)
class MetadadosVideo:
    """Propriedades do vídeo, lidas uma vez na abertura."""

    fps: float
    largura: int
    altura: int
    total_quadros: int | None
    origem: str

    @property
    def resolucao(self) -> tuple[int, int]:
        return (self.largura, self.altura)


@dataclass(frozen=True, slots=True)
class Quadro:
    """Um frame lido do vídeo e o seu instante em segundos."""

    indice: int
    imagem: object
    """Frame BGR do OpenCV (``numpy.ndarray``). Tipado como ``object`` para que
    o módulo de modelos não dependa de numpy; use ``video.quadros.is_bgr``."""

    tempo_s: float


__all__ = [
    "Caixa",
    "Deteccao",
    "MetadadosVideo",
    "Poligono",
    "Ponto",
    "Quadro",
]
