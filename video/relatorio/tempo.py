"""Tempo em desvio: união de intervalos, para o "% do tempo em desvio" do resumo.

Dois desvios podem acontecer ao mesmo tempo (uma queda e um giro de tronco, ou duas
assimetrias). Somar as durações contaria esse trecho duas vezes e o percentual poderia passar
de 100%; por isso os intervalos são unidos antes da soma.
"""

from __future__ import annotations

from collections.abc import Iterable

Intervalo = tuple[float, float]


def unir(intervalos: Iterable[Intervalo], *, limite: float | None = None) -> list[Intervalo]:
    """Une intervalos que se sobrepõem ou se encostam, cortando em ``[0, limite]``.

    ``limite`` é a duração do vídeo: um desvio que vai "até o fim do vídeo" não pode contar
    além dele. Intervalos vazios ou fora do vídeo somem.
    """
    validos: list[Intervalo] = []
    for inicio, fim in intervalos:
        inicio = max(0.0, inicio)
        if limite is not None:
            fim = min(fim, limite)
        if fim > inicio:
            validos.append((inicio, fim))
    validos.sort()
    unidos: list[Intervalo] = []
    for inicio, fim in validos:
        if unidos and inicio <= unidos[-1][1]:
            unidos[-1] = (unidos[-1][0], max(unidos[-1][1], fim))
        else:
            unidos.append((inicio, fim))
    return unidos


def total(intervalos: Iterable[Intervalo]) -> float:
    """Soma das durações de intervalos já unidos."""
    return sum(fim - inicio for inicio, fim in intervalos)


def percentual(parte: float, todo: float | None) -> float | None:
    """``parte`` como % de ``todo`` (2 casas); ``None`` se ``todo`` é desconhecido ou zero."""
    if todo is None or todo <= 0:
        return None
    return round(100 * min(parte, todo) / todo, 2)


__all__ = ["Intervalo", "percentual", "total", "unir"]
