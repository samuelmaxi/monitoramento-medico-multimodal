"""Operações sobre séries por quadro: trechos persistentes e saltos mantidos.

Tudo aqui recebe arrays numpy alinhados por quadro processado (tempo em
segundos, valor da medida) e devolve índices. Não conhece ângulos, perfis nem
eventos, o que deixa as regras de :mod:`video.desvios.regras` curtas.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np

_EPS = 1e-9


def periodo_amostral(tempos_s: np.ndarray) -> float:
    """Intervalo típico entre quadros processados (mediana das diferenças)."""
    if len(tempos_s) < 2:
        return 0.0
    return float(np.median(np.diff(tempos_s)))


def trechos(
    tempos_s: np.ndarray,
    mascara: np.ndarray,
    *,
    janela_s: float,
    lacuna_max_s: float,
) -> list[tuple[int, int]]:
    """Trechos ``[ini, fim]`` (índices inclusivos) em que ``mascara`` fica verdadeira.

    Dois trechos separados por uma lacuna de até ``lacuna_max_s`` (quadros
    falsos ou sem medida) viram um só. Depois da fusão, só ficam os trechos que
    duram ``janela_s`` ou mais, contando o período do último quadro.
    """
    indices = np.flatnonzero(mascara)
    if indices.size == 0:
        return []
    periodo = periodo_amostral(tempos_s)
    grupos: list[list[int]] = [[int(indices[0]), int(indices[0])]]
    for i in indices[1:]:
        lacuna = tempos_s[i] - tempos_s[grupos[-1][1]] - periodo
        if lacuna <= lacuna_max_s + _EPS:
            grupos[-1][1] = int(i)
        else:
            grupos.append([int(i), int(i)])
    return [
        (ini, fim)
        for ini, fim in grupos
        if tempos_s[fim] - tempos_s[ini] + periodo >= janela_s - _EPS
    ]


def interpolar_lacunas(
    valores: np.ndarray, tempos_s: np.ndarray, lacuna_max_s: float
) -> np.ndarray:
    """Preenche NaN por interpolação linear só onde o buraco é curto.

    Um keypoint que some por um ou dois quadros não deve partir uma repetição ao
    meio; um buraco longo continua NaN porque não dá para saber o que houve.
    """
    saida = valores.astype(float).copy()
    validos = np.flatnonzero(~np.isnan(saida))
    limite = lacuna_max_s + periodo_amostral(tempos_s) + _EPS
    for a, b in itertools.pairwise(validos):
        if b - a > 1 and tempos_s[b] - tempos_s[a] <= limite:
            saida[a + 1 : b] = np.interp(
                tempos_s[a + 1 : b], [tempos_s[a], tempos_s[b]], [saida[a], saida[b]]
            )
    return saida


@dataclass(frozen=True)
class Salto:
    """Mudança de ``limiar`` ou mais em até ``tempo_max_s`` que se manteve."""

    ref: int
    """Quadro de referência, antes da mudança."""
    impacto: int
    """Primeiro quadro em que a mudança passou o limiar."""
    fim: int
    """Último quadro do trecho em que a posição nova se manteve."""
    variacao: float
    """Maior variação em relação à referência durante o trecho mantido (com sinal)."""
    pico: int
    """Quadro da maior variação."""


def saltos_mantidos(
    tempos_s: np.ndarray,
    valores: np.ndarray,
    *,
    limiar: float,
    tempo_max_s: float,
    janela_s: float,
    lacuna_max_s: float,
    fracao_permanencia: float,
    sinal: int,
    ausencia_mantem: bool = False,
) -> list[Salto]:
    """Saltos de ``valores`` no sentido ``sinal`` (+1 sobe, -1 desce) que não se desfazem.

    Para cada quadro ``i``, a referência é o extremo oposto (mínimo se ``sinal``
    é +1) entre os quadros dos ``tempo_max_s`` anteriores. Se ``valores[i]``
    passou ``limiar`` dessa referência, a mudança foi rápida o bastante. Ela só
    conta se o valor ficar além de ``fracao_permanencia * limiar`` da referência
    por ``janela_s`` (com lacunas toleradas), a partir de ``i``. Um vídeo que
    termina antes de completar a janela não confirma o salto.

    Com ``ausencia_mantem``, quadro sem medida não interrompe a permanência: só
    uma medida de volta aquém do limiar desfaz o salto. É o caso da queda, em
    que a pessoa no chão costuma sair do quadro ou ficar ocluída. O trecho
    termina na última medida válida, então a janela ainda exige observação.
    """
    v = np.asarray(valores, dtype=float) * sinal
    n = len(v)
    periodo = periodo_amostral(tempos_s)
    saltos: list[Salto] = []
    i = 0
    while i < n:
        if np.isnan(v[i]):
            i += 1
            continue
        anteriores = np.flatnonzero(
            (tempos_s < tempos_s[i]) & (tempos_s >= tempos_s[i] - tempo_max_s - _EPS) & ~np.isnan(v)
        )
        if anteriores.size == 0:
            i += 1
            continue
        ref = int(anteriores[np.argmin(v[anteriores])])
        if v[i] - v[ref] < limiar:
            i += 1
            continue
        with np.errstate(invalid="ignore"):
            embaixo = v[i:] - v[ref] >= fracao_permanencia * limiar
        mantido = embaixo | np.isnan(v[i:]) if ausencia_mantem else embaixo
        primeiro = trechos(tempos_s[i:], mantido, janela_s=0.0, lacuna_max_s=lacuna_max_s)
        if not primeiro or primeiro[0][0] != 0:
            i += 1
            continue
        fim = primeiro[0][1]
        while not embaixo[fim]:  # termina na última medida válida que confirma o salto
            fim -= 1
        fim += i
        if tempos_s[fim] - tempos_s[i] + periodo < janela_s - _EPS:
            i += 1
            continue
        trecho = v[i : fim + 1] - v[ref]
        pico = i + int(np.nanargmax(trecho))
        saltos.append(
            Salto(
                ref=ref,
                impacto=i,
                fim=fim,
                variacao=float(sinal * (v[pico] - v[ref])),
                pico=pico,
            )
        )
        i = fim + 1
    return saltos
