"""Casos rotulados por injeção sobre séries REAIS do MIMIC-IV Demo.

O demo não tem quedas, saídas noturnas nem episódios de agitação rotulados.
Cada caso pega a série real de uma estadia até um instante sorteado (logo
depois de um registro real, para não começar no meio de uma lacuna) e injeta
um padrão conhecido. Assim o detector é medido sobre o histórico real do
paciente, inclusive a baseline dele.

==========================  ======================================  ==========
cenário                     injeção                                 esperado
==========================  ======================================  ==========
``queda_dia/noite``         ``CHAO`` com queda                      queda alta
``saida_noite``             fora do leito à noite, volta em 20 min  saída alta
``saida_dia_controle``      o mesmo de dia                          sem saída alta
``imobilidade``             130-240 min sem mudança (registro       imobilidade
                            a cada 30 min)
``imobilidade_controle``    60-100 min sem mudança                  nada
``agitacao``                6 h com max(3, 3 x baseline) trocas/h   agitação
``reducao``                 6 h sem troca (registro a cada 30 min)  redução
==========================  ======================================  ==========

Agitação e redução só usam estadias com baseline válida no instante sorteado
(``baseline_min_h`` horas e ``minimo_eventos_baseline`` eventos antes).
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from contratos import Severidade

from .config import ConfigMovimentacao
from .modelos import NO_LEITO, Observacao, Posicao
from .serie import marcos, periodo

FONTE = "injecao"
_CICLO = (Posicao.SUPINO, Posicao.LATERAL_ESQ, Posicao.LATERAL_DIR)

CENARIOS = (
    "queda_dia",
    "queda_noite",
    "saida_noite",
    "saida_dia_controle",
    "imobilidade",
    "imobilidade_controle",
    "agitacao",
    "reducao",
)


@dataclass(frozen=True)
class Caso:
    id: str
    cenario: str
    event_type: str
    positivo: bool
    inicio: datetime
    fim: datetime
    severidade: Severidade | None
    """Positivo: a severidade que o achado precisa ter. Controle: a que não pode aparecer
    (``None`` = nenhum achado do tipo pode aparecer)."""
    observacoes: list[Observacao] = field(repr=False)
    descricao: str


def _ultima_no_leito(serie: Sequence[Observacao]) -> Posicao:
    for o in reversed(serie):
        if o.posicao in NO_LEITO:
            return o.posicao
    return Posicao.SUPINO


def _outra(posicao: Posicao, rng: random.Random) -> Posicao:
    return rng.choice([p for p in _CICLO if p is not posicao])


def _taxa_baseline(
    serie: Sequence[Observacao], inicio: datetime, cfg: ConfigMovimentacao
) -> float | None:
    """Taxa de atividade (eventos/h) das ``janela_h`` antes de ``inicio``, se houver baseline."""
    b = cfg.baseline
    t0 = max(serie[0].instante, inicio - timedelta(hours=b.janela_h))
    horas = (inicio - t0).total_seconds() / 3600
    n = sum(
        t0 <= m.instante < inicio and m.tipo in ("mudanca_posicao", "saida_leito")
        for m in marcos(serie, cfg)
    )
    if horas < b.baseline_min_h or n < b.minimo_eventos_baseline:
        return None
    return n / horas


def _inicio(
    serie: Sequence[Observacao],
    cfg: ConfigMovimentacao,
    rng: random.Random,
    *,
    historico_h: float,
    periodo_alvo: str | None = None,
) -> datetime | None:
    """Um minuto depois de um registro real sorteado, com histórico e período pedidos."""
    minimo = serie[0].instante + timedelta(hours=historico_h)
    candidatos = [
        o.instante + timedelta(minutes=1)
        for o in serie
        if o.instante >= minimo
        and periodo_alvo in (None, periodo(o.instante + timedelta(minutes=1), cfg))
    ]
    return rng.choice(candidatos) if candidatos else None


def _caso(cenario: str, k: int, chave: tuple[str, ...], rng: random.Random, serie, cfg):
    """Monta um caso do cenário sobre ``serie`` ou devolve ``None`` se a estadia não serve."""
    ident = f"{cenario}-{k}-{chave[-1]}"
    min_ = timedelta(minutes=1)

    if cenario in ("queda_dia", "queda_noite"):
        alvo = cenario.removeprefix("queda_")
        inicio = _inicio(serie, cfg, rng, historico_h=2, periodo_alvo=alvo)
        if inicio is None:
            return None
        antes = [o for o in serie if o.instante < inicio]
        inj = [Observacao(inicio, Posicao.CHAO, FONTE, queda=True)]
        return Caso(ident, cenario, "queda", True, inicio, inicio, Severidade.ALTA,
                    antes + inj, f"queda injetada às {inicio:%H:%M} ({alvo})")

    if cenario in ("saida_noite", "saida_dia_controle"):
        alvo = "noite" if cenario == "saida_noite" else "dia"
        inicio = _inicio(serie, cfg, rng, historico_h=2, periodo_alvo=alvo)
        if inicio is None:
            return None
        antes = [o for o in serie if o.instante < inicio - min_]
        no_leito = _ultima_no_leito(antes)
        inj = [
            Observacao(inicio - min_, no_leito, FONTE),
            Observacao(inicio, Posicao.FORA_DO_LEITO, FONTE),
            Observacao(inicio + timedelta(minutes=20), no_leito, FONTE),
        ]
        positivo = alvo == "noite"
        return Caso(ident, cenario, "saida_do_leito", positivo, inicio, inicio, Severidade.ALTA,
                    antes + inj, f"saída do leito injetada às {inicio:%H:%M} ({alvo})")

    if cenario in ("imobilidade", "imobilidade_controle"):
        inicio = _inicio(serie, cfg, rng, historico_h=1)
        if inicio is None:
            return None
        positivo = cenario == "imobilidade"
        minutos = rng.randint(130, 240) if positivo else rng.randint(60, 100)
        antes = [o for o in serie if o.instante < inicio]
        parado = _outra(_ultima_no_leito(antes), rng)
        inj = [
            Observacao(inicio + timedelta(minutes=m), parado, FONTE)
            for m in range(0, minutos, 30)
        ]
        fim = inicio + timedelta(minutes=minutos)
        inj.append(Observacao(fim, _outra(parado, rng), FONTE))
        return Caso(ident, cenario, "imobilidade_prolongada", positivo, inicio, fim, None,
                    antes + inj, f"{minutos} min sem mudança injetados às {inicio:%H:%M}")

    if cenario in ("agitacao", "reducao"):
        inicio = _inicio(serie, cfg, rng, historico_h=cfg.baseline.baseline_min_h)
        if inicio is None:
            return None
        antes = [o for o in serie if o.instante < inicio]
        taxa = _taxa_baseline(antes, inicio, cfg)
        if taxa is None:
            return None
        horas = cfg.baseline.janela_atual_h
        fim = inicio + timedelta(hours=horas)
        atual = _ultima_no_leito(antes)
        inj = []
        if cenario == "agitacao":
            por_hora = max(3.0, 3 * taxa)
            passo = timedelta(hours=1) / por_hora
            t = inicio
            while t <= fim:
                atual = _outra(atual, rng)
                inj.append(Observacao(t, atual, FONTE))
                t += passo
            tipo, desc = "agitacao_subita", f"{por_hora:.1f} trocas/h contra {taxa:.2f}/h"
        else:
            t = inicio
            while t <= fim:
                inj.append(Observacao(t, atual, FONTE))
                t += timedelta(minutes=30)
            tipo, desc = "reducao_mobilidade", f"6 h sem troca contra {taxa:.2f}/h"
        return Caso(ident, cenario, tipo, True, inicio, fim, None, antes + inj,
                    f"{desc}, a partir de {inicio:%H:%M}")

    raise ValueError(f"cenário desconhecido: {cenario}")


def gerar_casos(
    series: Mapping[tuple[str, ...], Sequence[Observacao]],
    cfg: ConfigMovimentacao,
    *,
    seed: int,
    n_por_tipo: int,
    tentativas: int = 50,
) -> list[Caso]:
    """``n_por_tipo`` casos de cada cenário, reprodutíveis pela ``seed``."""
    rng = random.Random(seed)
    chaves = sorted(k for k, v in series.items() if v)
    casos: list[Caso] = []
    for cenario in CENARIOS:
        for k in range(n_por_tipo):
            for _ in range(tentativas):
                chave = rng.choice(chaves)
                caso = _caso(cenario, k, chave, rng, list(series[chave]), cfg)
                if caso is not None:
                    casos.append(caso)
                    break
    return casos


__all__ = ["CENARIOS", "Caso", "gerar_casos"]
