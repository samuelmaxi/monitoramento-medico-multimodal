"""Detectores da US17 sobre a série de movimentação.

==========================  ====================================================
``event_type``              Regra
==========================  ====================================================
``imobilidade_prolongada``  trecho confiável no leito sem mudança > ``limiar_min``
``saida_do_leito``          cada saída; severidade pelo período (alta à noite)
``queda``                   cada queda, sempre alta; fontes a ≤ ``fusao_janela_s``
                            viram um evento só
``agitacao_subita``         taxa de movimentação das últimas ``janela_atual_h``
                            ≥ ``agitacao_fator`` x baseline do próprio paciente
``reducao_mobilidade``      a mesma taxa ≤ ``reducao_fator`` x baseline
==========================  ====================================================

A baseline é a taxa média (mudanças de posição + saídas do leito por hora) das
``janela_h`` horas anteriores à janela atual. Média e não mediana das taxas
horárias: com registro de enfermagem a cada ~2 h, metade das horas tem zero
eventos e a mediana seria zero. A comparação só acontece com pelo menos
``baseline_min_h`` horas e ``minimo_eventos_baseline`` eventos de histórico, e
com a janela atual inteira observada (sem lacuna > ``lacuna_max_min``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any

from contratos import Severidade

from .config import ConfigMovimentacao
from .modelos import Anomalia, Marco, Observacao
from .serie import marcos, metricas, ordenar, periodo, trechos_sem_mudanca

TIPOS = (
    "imobilidade_prolongada",
    "saida_do_leito",
    "queda",
    "agitacao_subita",
    "reducao_mobilidade",
)

# Ponto médio das faixas de ``contratos.severidade_sugerida``: o score fica
# coerente com a severidade quando é a regra clínica que decide a severidade.
_SCORE_DA_SEVERIDADE = {
    Severidade.INFO: 0.1,
    Severidade.BAIXA: 0.4,
    Severidade.MEDIA: 0.6,
    Severidade.ALTA: 0.9,
}

_PERIODO = {"dia": "período diurno", "noite": "período noturno"}

_ATIVIDADE = frozenset({"mudanca_posicao", "saida_leito"})


def _duracao(minutos: float) -> str:
    h, m = divmod(round(minutos), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def _hora(instante: datetime, cfg: ConfigMovimentacao) -> str:
    return instante.astimezone(cfg.fuso).strftime("%H:%M")


def _n(valor: float) -> str:
    return f"{valor:.2f}".replace(".", ",")


def _fontes(serie: Sequence[Observacao], ini: datetime, fim: datetime) -> tuple[str, ...]:
    return tuple(sorted({o.fonte for o in serie if ini <= o.instante <= fim}))


def _metricas_em(serie: Sequence[Observacao], cfg: ConfigMovimentacao, t: datetime) -> dict:
    return metricas([o for o in serie if o.instante <= t], cfg, ate=t)


# --------------------------------------------------------------------------- #
# Imobilidade
# --------------------------------------------------------------------------- #


def _imobilidade(serie, cfg: ConfigMovimentacao, ate) -> list[Anomalia]:
    regra = cfg.imobilidade
    achados = []
    for t in trechos_sem_mudanca(serie, cfg, ate=ate):
        if not t.confiavel or t.minutos <= regra.limiar_min:
            continue
        severidade = regra.severidade
        for minutos, sev in regra.severidade_acima_de_min.items():
            if t.minutos >= minutos:
                severidade = sev
        faixa = cfg.saturacao_imobilidade_min - regra.limiar_min
        excesso = (t.minutos - regra.limiar_min) / faixa
        achados.append(
            Anomalia(
                event_type="imobilidade_prolongada",
                inicio=t.inicio,
                fim=t.fim,
                severidade=severidade,
                score=round(0.5 + 0.5 * min(1.0, excesso), 3),
                resumo=f"{_duracao(t.minutos)} sem mudança de posição no leito",
                features={
                    **_metricas_em(serie, cfg, t.fim),
                    "minutos_sem_mudanca": round(t.minutos, 1),
                    "posicao": t.posicao.value,
                    "em_curso": t.aberto,
                    "alerta_em": (t.inicio + timedelta(minutes=regra.limiar_min)).isoformat(),
                    "maior_lacuna_min": round(t.maior_lacuna_min, 1),
                },
                fontes=_fontes(serie, t.inicio, t.fim),
                thresholds={
                    "limiar_min": regra.limiar_min,
                    "lacuna_max_min": cfg.lacuna_max_min,
                    "severidade_acima_de_min": {
                        str(int(k)): v.value for k, v in regra.severidade_acima_de_min.items()
                    },
                },
            )
        )
    return achados


# --------------------------------------------------------------------------- #
# Saída do leito e queda
# --------------------------------------------------------------------------- #


def _noite(cfg: ConfigMovimentacao) -> str:
    return f"{cfg.noite.inicio:%H:%M}-{cfg.noite.fim:%H:%M} ({cfg.fuso.key})"


def _saidas(serie, ms: list[Marco], cfg: ConfigMovimentacao) -> list[Anomalia]:
    regra = cfg.saida_leito
    achados = []
    for m in ms:
        if m.tipo != "saida_leito":
            continue
        sev = regra.severidade_noite if m.periodo == "noite" else regra.severidade_dia
        achados.append(
            Anomalia(
                event_type="saida_do_leito",
                inicio=m.instante,
                fim=m.instante,
                severidade=sev,
                score=_SCORE_DA_SEVERIDADE[sev],
                resumo=f"Saída do leito às {_hora(m.instante, cfg)} ({_PERIODO[m.periodo]})",
                features={
                    **_metricas_em(serie, cfg, m.instante),
                    "periodo": m.periodo,
                    "posicao_anterior": m.de.value,
                },
                fontes=(m.fonte,),
                thresholds={"periodo_noturno": _noite(cfg)},
            )
        )
    return achados


def _quedas(serie, ms: list[Marco], cfg: ConfigMovimentacao) -> list[Anomalia]:
    janela = timedelta(seconds=cfg.queda.fusao_janela_s)
    grupos: list[list[Marco]] = []
    for m in (m for m in ms if m.tipo == "queda"):
        if grupos and m.instante - grupos[-1][-1].instante <= janela:
            grupos[-1].append(m)
        else:
            grupos.append([m])
    achados = []
    for g in grupos:
        fontes = tuple(sorted({m.fonte for m in g}))
        achados.append(
            Anomalia(
                event_type="queda",
                inicio=g[0].instante,
                fim=g[-1].instante,
                severidade=cfg.queda.severidade,
                score=1.0,
                resumo=f"Queda às {_hora(g[0].instante, cfg)} ({_PERIODO[g[0].periodo]}), "
                f"detectada por {' + '.join(fontes)}",
                features={
                    **_metricas_em(serie, cfg, g[-1].instante),
                    "periodo": g[0].periodo,
                    "posicao_anterior": g[0].de.value,
                    "deteccoes": len(g),
                },
                fontes=fontes,
                thresholds={"fusao_janela_s": cfg.queda.fusao_janela_s},
            )
        )
    return achados


# --------------------------------------------------------------------------- #
# Baseline individual
# --------------------------------------------------------------------------- #


def _observada(instantes: Sequence[datetime], ini: datetime, fim: datetime, lacuna: float) -> bool:
    """A janela [ini, fim] tem observações sem buraco maior que ``lacuna`` minutos."""
    dentro = [ini, *(t for t in instantes if ini <= t <= fim), fim]
    return all((b - a).total_seconds() / 60 <= lacuna for a, b in pairwise(dentro))


def _baseline(serie, ms: list[Marco], cfg: ConfigMovimentacao, ate) -> list[Anomalia]:
    if not serie:
        return []
    regra = cfg.baseline
    hora = timedelta(hours=1)
    atual_h = timedelta(hours=regra.janela_atual_h)
    instantes = [o.instante for o in serie]
    atividade = [m.instante for m in ms if m.tipo in _ATIVIDADE]
    t0 = instantes[0]
    fim = max(ate or instantes[-1], instantes[-1])

    def contar(ini: datetime, f: datetime) -> int:
        return sum(ini <= t < f for t in atividade)

    sinais: list[tuple[str, datetime, dict[str, Any]]] = []
    t = t0 + timedelta(hours=regra.baseline_min_h) + atual_h
    while t <= fim:
        ini_atual = t - atual_h
        ini_base = max(t0, ini_atual - timedelta(hours=regra.janela_h))
        horas_base = (ini_atual - ini_base) / hora
        n_base = contar(ini_base, ini_atual)
        if (
            horas_base >= regra.baseline_min_h
            and n_base >= regra.minimo_eventos_baseline
            and _observada(instantes, ini_atual, t, cfg.lacuna_max_min)
        ):
            taxa_base = n_base / horas_base
            taxa = contar(ini_atual, t) / regra.janela_atual_h
            tipo = None
            if taxa >= regra.agitacao_fator * taxa_base:
                tipo = "agitacao_subita"
            elif taxa <= regra.reducao_fator * taxa_base:
                tipo = "reducao_mobilidade"
            if tipo:
                sinais.append((tipo, t, {
                    "taxa_atual_por_hora": round(taxa, 3),
                    "taxa_baseline_por_hora": round(taxa_base, 3),
                    "razao": round(taxa / taxa_base, 3),
                    "horas_baseline": round(horas_base, 1),
                }))
        t += hora

    # Avaliações seguidas com o mesmo sinal são um episódio só.
    episodios: list[list[tuple[str, datetime, dict[str, Any]]]] = []
    for s in sinais:
        if episodios and episodios[-1][-1][0] == s[0] and s[1] - episodios[-1][-1][1] <= hora:
            episodios[-1].append(s)
        else:
            episodios.append([s])

    achados = []
    for ep in episodios:
        tipo, primeiro, f = ep[0]
        ultimo = ep[-1][1]
        if tipo == "agitacao_subita":
            resumo = (
                f"Movimentação {_n(f['razao'])}x a baseline do paciente "
                f"({_n(f['taxa_atual_por_hora'])}/h contra {_n(f['taxa_baseline_por_hora'])}/h)"
            )
        else:
            resumo = (
                f"Movimentação caiu para {_n(f['taxa_atual_por_hora'])}/h "
                f"contra {_n(f['taxa_baseline_por_hora'])}/h da baseline do paciente"
            )
        # 0,5 no limiar; 1,0 com o dobro do fator (agitação) ou parado de vez (redução).
        if tipo == "agitacao_subita":
            excesso = (f["razao"] - regra.agitacao_fator) / regra.agitacao_fator
        else:
            excesso = (regra.reducao_fator - f["razao"]) / regra.reducao_fator
        achados.append(
            Anomalia(
                event_type=tipo,
                inicio=primeiro - atual_h,
                fim=ultimo,
                severidade=regra.severidade,
                score=round(0.5 + 0.5 * min(1.0, max(0.0, excesso)), 3),
                resumo=resumo,
                features={**_metricas_em(serie, cfg, ultimo), **f, "avaliacoes": len(ep)},
                fontes=_fontes(serie, primeiro - atual_h, ultimo),
                thresholds={
                    "janela_h": regra.janela_h,
                    "janela_atual_h": regra.janela_atual_h,
                    "baseline_min_h": regra.baseline_min_h,
                    "minimo_eventos_baseline": regra.minimo_eventos_baseline,
                    "agitacao_fator": regra.agitacao_fator,
                    "reducao_fator": regra.reducao_fator,
                },
            )
        )
    return achados


# --------------------------------------------------------------------------- #
# Entrada
# --------------------------------------------------------------------------- #


def detectar(
    observacoes: Sequence[Observacao],
    cfg: ConfigMovimentacao,
    *,
    tipos: Iterable[str] | None = None,
    ate: datetime | None = None,
) -> list[Anomalia]:
    """Todas as anomalias da série, em ordem de início.

    ``ate`` é o "agora" da análise: um trecho sem mudança ainda aberto é
    medido até ele (padrão: a última observação).
    """
    pedidos = set(TIPOS if tipos is None else tipos)
    serie = ordenar(observacoes)
    ms = marcos(serie, cfg)
    achados: list[Anomalia] = []
    if "imobilidade_prolongada" in pedidos:
        achados += _imobilidade(serie, cfg, ate)
    if "saida_do_leito" in pedidos:
        achados += _saidas(serie, ms, cfg)
    if "queda" in pedidos:
        achados += _quedas(serie, ms, cfg)
    if pedidos & {"agitacao_subita", "reducao_mobilidade"}:
        achados += [a for a in _baseline(serie, ms, cfg, ate) if a.event_type in pedidos]
    return sorted(achados, key=lambda a: (a.inicio, a.event_type))


__all__ = ["TIPOS", "detectar", "periodo"]
