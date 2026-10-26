"""Divisão de texto por sentença para respeitar os limites do Azure AI Language.

O sentimento síncrono aceita até 5.120 caracteres por documento. Uma fala maior
é dividida em pedaços que terminam em fim de sentença; uma sentença sozinha
acima do limite é cortada no último espaço antes dele. Cada pedaço guarda o
``offset`` em relação ao texto original, para os offsets devolvidos pelo
serviço voltarem à posição certa na fala.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence, TypeVar

T = TypeVar("T")

# Fim de sentença: . ! ? … (e reticências) seguidos de espaço.
_FIM_DE_SENTENCA = re.compile(r"(?<=[.!?…])\s+")


@dataclass(frozen=True)
class Chunk:
    offset: int
    text: str

    @property
    def end(self) -> int:
        return self.offset + len(self.text)


def sentence_spans(texto: str) -> list[tuple[int, int]]:
    """Intervalos ``(início, fim)`` de cada sentença, sem os espaços entre elas."""
    spans: list[tuple[int, int]] = []
    inicio = 0
    for separador in _FIM_DE_SENTENCA.finditer(texto):
        spans.append((inicio, separador.start()))
        inicio = separador.end()
    spans.append((inicio, len(texto)))
    resultado = []
    for a, b in spans:
        trecho = texto[a:b]
        a += len(trecho) - len(trecho.lstrip())
        b -= len(trecho) - len(trecho.rstrip())
        if b > a:
            resultado.append((a, b))
    return resultado


def _cortar_sentenca_longa(texto: str, inicio: int, fim: int, max_chars: int) -> Iterator[tuple[int, int]]:
    while fim - inicio > max_chars:
        corte = texto.rfind(" ", inicio + 1, inicio + max_chars + 1)
        if corte <= inicio:
            corte = inicio + max_chars  # palavra maior que o limite: corte seco
        yield inicio, corte
        inicio = corte
        while inicio < fim and texto[inicio].isspace():
            inicio += 1
    if fim > inicio:
        yield inicio, fim


def chunk_text(texto: str, max_chars: int) -> list[Chunk]:
    """Agrupa sentenças consecutivas em pedaços de até ``max_chars`` caracteres."""
    if max_chars <= 0:
        raise ValueError("max_chars deve ser positivo")
    pedacos: list[tuple[int, int]] = []
    for a, b in sentence_spans(texto):
        pedacos.extend(_cortar_sentenca_longa(texto, a, b, max_chars))

    chunks: list[Chunk] = []
    atual: tuple[int, int] | None = None
    for a, b in pedacos:
        if atual is not None and b - atual[0] <= max_chars:
            atual = (atual[0], b)
            continue
        if atual is not None:
            chunks.append(Chunk(atual[0], texto[atual[0]:atual[1]]))
        atual = (a, b)
    if atual is not None:
        chunks.append(Chunk(atual[0], texto[atual[0]:atual[1]]))
    return chunks


def sentence_at(texto: str, offset: int) -> str:
    """Sentença que contém a posição ``offset`` (o texto inteiro se não achar)."""
    for a, b in sentence_spans(texto):
        if a <= offset < b:
            return texto[a:b]
    return texto


def batched(itens: Sequence[T] | Iterable[T], tamanho: int) -> Iterator[list[T]]:
    """Lotes de até ``tamanho`` itens, na ordem original."""
    if tamanho <= 0:
        raise ValueError("tamanho do lote deve ser positivo")
    lote: list[T] = []
    for item in itens:
        lote.append(item)
        if len(lote) == tamanho:
            yield lote
            lote = []
    if lote:
        yield lote
