"""Série de movimentação: marcos, trechos sem mudança, período dia/noite e métricas.

A série é uma lista de :class:`~movimentacao.modelos.Observacao` de qualquer
fonte, em qualquer ordem. Daqui saem:

- **marcos**: mudança de posição, saída e retorno ao leito, queda;
- **trechos sem mudança**: período no leito entre dois marcos, base do alerta
  de imobilidade. Um trecho com lacuna de observação maior que
  ``lacuna_max_min`` fica ``confiavel=False``: sem dado não dá para afirmar
  que o paciente ficou imóvel;
- **métricas** do DoD: tempo sem mudança, reposicionamentos por hora, saídas.

``ROTATIVO`` (cama terapêutica) encerra o trecho sem contar como
reposicionamento; ``DESCONHECIDA`` não muda nada, só conta como observação.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from .config import ConfigMovimentacao
from .modelos import NO_LEITO, Marco, Observacao, Periodo, Posicao, TipoMarco

_FORA = frozenset({Posicao.FORA_DO_LEITO, Posicao.CHAO})


@dataclass(frozen=True)
class Trecho:
    """Período no leito sem mudança de posição registrada."""

    inicio: datetime
    fim: datetime
    posicao: Posicao
    confiavel: bool
    aberto: bool
    """O trecho não terminou num marco: vai até o fim da série (ou ``ate``)."""
    maior_lacuna_min: float

    @property
    def minutos(self) -> float:
        return (self.fim - self.inicio).total_seconds() / 60


def periodo(instante: datetime, cfg: ConfigMovimentacao) -> Periodo:
    hora = instante.astimezone(cfg.fuso).time()
    inicio, fim = cfg.noite.inicio, cfg.noite.fim
    # inicio > fim: a noite cruza a meia-noite (22:00 a 06:00).
    noite = inicio <= hora < fim if inicio <= fim else (hora >= inicio or hora < fim)
    return "noite" if noite else "dia"


def ordenar(observacoes: Sequence[Observacao]) -> list[Observacao]:
    return sorted(observacoes, key=lambda o: o.instante)


def _marco_de(atual: Posicao | None, o: Observacao) -> tuple[TipoMarco, Posicao] | None:
    """Que marco ``o`` gera dado o estado ``atual``, e o novo estado."""
    if o.queda or (o.posicao is Posicao.CHAO and atual is not Posicao.CHAO):
        return "queda", Posicao.CHAO
    if o.posicao is Posicao.FORA_DO_LEITO and atual not in _FORA:
        return "saida_leito", o.posicao
    if o.posicao in NO_LEITO and atual in _FORA:
        return "retorno_leito", o.posicao
    if o.posicao in NO_LEITO and atual in NO_LEITO and o.posicao is not atual:
        return "mudanca_posicao", o.posicao
    if o.mudanca and atual in NO_LEITO and o.posicao in (atual, Posicao.DESCONHECIDA):
        return "mudanca_posicao", atual
    return None


def _proximo_estado(atual: Posicao | None, o: Observacao) -> Posicao | None:
    # DESCONHECIDA não apaga o que se sabia; CHAO só sai quando outra posição chega.
    return atual if o.posicao is Posicao.DESCONHECIDA else o.posicao


def _percorrer(observacoes: Sequence[Observacao], cfg: ConfigMovimentacao):
    """Gera ``(observacao, estado_anterior, marco | None)`` em ordem de tempo."""
    atual: Posicao | None = None
    for o in ordenar(observacoes):
        achado = _marco_de(atual, o)
        marco = None
        if achado:
            tipo, para = achado
            de = atual if atual is not None else Posicao.DESCONHECIDA
            marco = Marco(tipo, o.instante, periodo(o.instante, cfg), de, para, o.fonte)
            atual = para if tipo == "queda" else _proximo_estado(atual, o)
        else:
            atual = _proximo_estado(atual, o)
        yield o, atual, marco


def marcos(observacoes: Sequence[Observacao], cfg: ConfigMovimentacao) -> list[Marco]:
    return [m for _, _, m in _percorrer(observacoes, cfg) if m is not None]


def trechos_sem_mudanca(
    observacoes: Sequence[Observacao],
    cfg: ConfigMovimentacao,
    ate: datetime | None = None,
) -> list[Trecho]:
    trechos: list[Trecho] = []
    inicio: datetime | None = None
    posicao = Posicao.DESCONHECIDA
    ultimo: datetime | None = None
    maior_lacuna = 0.0

    def fechar(fim: datetime, aberto: bool) -> None:
        nonlocal inicio, maior_lacuna
        if inicio is not None:
            lacuna = max(maior_lacuna, (fim - ultimo).total_seconds() / 60)  # type: ignore[operator]
            trechos.append(
                Trecho(inicio, fim, posicao, lacuna <= cfg.lacuna_max_min, aberto, lacuna)
            )
        inicio, maior_lacuna = None, 0.0

    for o, estado, marco in _percorrer(observacoes, cfg):
        if inicio is not None:
            maior_lacuna = max(maior_lacuna, (o.instante - ultimo).total_seconds() / 60)  # type: ignore[operator]
        if marco is not None or estado not in NO_LEITO:
            fechar(o.instante, aberto=False)
        if inicio is None and estado in NO_LEITO:
            inicio, posicao = o.instante, estado  # type: ignore[assignment]
        ultimo = o.instante

    if inicio is not None and ultimo is not None:
        fechar(max(ate or ultimo, ultimo), aberto=True)
    return trechos


def metricas(
    observacoes: Sequence[Observacao],
    cfg: ConfigMovimentacao,
    ate: datetime | None = None,
) -> dict[str, float | int]:
    """Métricas do DoD sobre a série inteira, medidas até ``ate`` (ou a última observação)."""
    serie = ordenar(observacoes)
    ms = marcos(serie, cfg)
    trechos = trechos_sem_mudanca(serie, cfg, ate=ate)
    horas = 0.0
    if serie:
        fim = max(ate or serie[-1].instante, serie[-1].instante)
        horas = (fim - serie[0].instante).total_seconds() / 3600
    mudancas = sum(m.tipo == "mudanca_posicao" for m in ms)
    saidas = [m for m in ms if m.tipo == "saida_leito"]
    confiaveis = [t.minutos for t in trechos if t.confiavel]
    atual = trechos[-1].minutos if trechos and trechos[-1].aberto else 0.0
    return {
        "minutos_sem_mudanca": round(atual, 1),
        "maior_minutos_sem_mudanca": round(max(confiaveis, default=0.0), 1),
        "reposicionamentos_por_hora": round(mudancas / horas, 3) if horas else 0.0,
        "mudancas_de_posicao": mudancas,
        "saidas_do_leito": len(saidas),
        "saidas_do_leito_noite": sum(m.periodo == "noite" for m in saidas),
        "quedas": sum(m.tipo == "queda" for m in ms),
        "horas_observadas": round(horas, 2),
    }


__all__ = ["Trecho", "marcos", "metricas", "ordenar", "periodo", "trechos_sem_mudanca"]
