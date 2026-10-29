"""Leitura do ``config/us17_movimentacao.yaml``: limiares da US17.

Mesmo contrato do ``video/desvios/config.py`` (US08): chave obrigatória
ausente, valor fora do domínio ou chave desconhecida geram
:class:`ErroDeConfiguracao` com o caminho da chave, na carga.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from contratos import Severidade

CAMINHO_PADRAO = Path("config/us17_movimentacao.yaml")

_RE_HORA = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ErroDeConfiguracao(ValueError):
    """Configuração da US17 ausente ou inválida."""


@dataclass(frozen=True)
class Noite:
    inicio: time
    fim: time


@dataclass(frozen=True)
class Imobilidade:
    limiar_min: float
    severidade: Severidade
    severidade_acima_de_min: Mapping[float, Severidade]


@dataclass(frozen=True)
class SaidaLeito:
    severidade_dia: Severidade
    severidade_noite: Severidade


@dataclass(frozen=True)
class Queda:
    severidade: Severidade
    fusao_janela_s: float


@dataclass(frozen=True)
class Baseline:
    janela_h: float
    janela_atual_h: float
    baseline_min_h: float
    minimo_eventos_baseline: int
    agitacao_fator: float
    reducao_fator: float
    severidade: Severidade


@dataclass(frozen=True)
class ConfigMovimentacao:
    versao: str
    fuso: ZoneInfo
    noite: Noite
    lacuna_max_min: float
    imobilidade: Imobilidade
    saida_leito: SaidaLeito
    queda: Queda
    baseline: Baseline
    saturacao_imobilidade_min: float
    video: Mapping[str, Any] = field(default_factory=dict)
    fonte: Path | None = None


# --------------------------------------------------------------------------- #
# Validação
# --------------------------------------------------------------------------- #


def _get(dados: Mapping[str, Any], chave: str, caminho: str) -> Any:
    if not isinstance(dados, Mapping) or chave not in dados:
        raise ErroDeConfiguracao(f"chave obrigatória ausente: '{caminho}.{chave}'")
    return dados[chave]


def _mapa(valor: Any, caminho: str) -> Mapping[str, Any]:
    if not isinstance(valor, Mapping):
        raise ErroDeConfiguracao(f"'{caminho}' precisa ser um mapeamento")
    return valor


def _bloco(dados: Mapping[str, Any], chave: str, permitidas: set[str]) -> Mapping[str, Any]:
    bloco = _mapa(_get(dados, chave, "raiz"), chave)
    extras = set(bloco) - permitidas
    if extras:
        raise ErroDeConfiguracao(f"chaves desconhecidas em '{chave}': {sorted(extras)}")
    return bloco


def _numero(valor: Any, caminho: str, *, minimo: float = 0.0) -> float:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ErroDeConfiguracao(f"'{caminho}' precisa ser um número (recebido: {valor!r})")
    if valor <= minimo:
        raise ErroDeConfiguracao(
            f"'{caminho}' precisa ser maior que {minimo} (recebido: {valor!r})"
        )
    return float(valor)


def _severidade(valor: Any, caminho: str) -> Severidade:
    try:
        return Severidade(valor)
    except ValueError:
        validas = ", ".join(s.value for s in Severidade)
        raise ErroDeConfiguracao(f"'{caminho}' = {valor!r}; use {validas}") from None


def _hora(valor: Any, caminho: str) -> time:
    m = _RE_HORA.match(str(valor))
    if not m:
        raise ErroDeConfiguracao(f"'{caminho}' = {valor!r}; use HH:MM (00:00 a 23:59)")
    return time(int(m[1]), int(m[2]))


def _fuso(valor: Any) -> ZoneInfo:
    try:
        return ZoneInfo(str(valor))
    except (ZoneInfoNotFoundError, ValueError):
        raise ErroDeConfiguracao(f"'fuso' = {valor!r} não é um fuso IANA válido") from None


def _imobilidade(dados: Mapping[str, Any]) -> Imobilidade:
    b = _bloco(dados, "imobilidade", {"limiar_min", "severidade", "severidade_acima_de_min"})
    limiar = _numero(_get(b, "limiar_min", "imobilidade"), "imobilidade.limiar_min")
    escala = {}
    bruto = _mapa(b.get("severidade_acima_de_min", {}), "imobilidade.severidade_acima_de_min")
    for minutos, sev in bruto.items():
        aqui = f"imobilidade.severidade_acima_de_min.{minutos}"
        escala[_numero(minutos, aqui, minimo=limiar)] = _severidade(sev, aqui)
    return Imobilidade(
        limiar_min=limiar,
        severidade=_severidade(_get(b, "severidade", "imobilidade"), "imobilidade.severidade"),
        severidade_acima_de_min=dict(sorted(escala.items())),
    )


def _baseline(dados: Mapping[str, Any]) -> Baseline:
    chaves = {
        "janela_h", "janela_atual_h", "baseline_min_h", "minimo_eventos_baseline",
        "agitacao_fator", "reducao_fator", "severidade",
    }
    b = _bloco(dados, "baseline", chaves)

    def num(chave: str, minimo: float = 0.0) -> float:
        return _numero(_get(b, chave, "baseline"), f"baseline.{chave}", minimo=minimo)

    reducao = num("reducao_fator")
    if reducao >= 1:
        raise ErroDeConfiguracao(f"'baseline.reducao_fator' precisa ser < 1 (recebido: {reducao})")
    baseline = Baseline(
        janela_h=num("janela_h"),
        janela_atual_h=num("janela_atual_h"),
        baseline_min_h=num("baseline_min_h"),
        minimo_eventos_baseline=int(num("minimo_eventos_baseline")),
        agitacao_fator=num("agitacao_fator", minimo=1.0),
        reducao_fator=reducao,
        severidade=_severidade(_get(b, "severidade", "baseline"), "baseline.severidade"),
    )
    if baseline.baseline_min_h > baseline.janela_h:
        raise ErroDeConfiguracao("'baseline.baseline_min_h' não pode passar de 'baseline.janela_h'")
    return baseline


def config_de_dict(dados: Mapping[str, Any], fonte: Path | None = None) -> ConfigMovimentacao:
    dados = _mapa(dados, "raiz")
    permitidas = {
        "versao", "fuso", "noite", "lacuna_max_min", "imobilidade", "saida_leito",
        "queda", "baseline", "score", "video",
    }
    extras = set(dados) - permitidas
    if extras:
        raise ErroDeConfiguracao(f"chaves desconhecidas na raiz: {sorted(extras)}")

    noite = _bloco(dados, "noite", {"inicio", "fim"})
    saida = _bloco(dados, "saida_leito", {"severidade_dia", "severidade_noite"})
    queda = _bloco(dados, "queda", {"severidade", "fusao_janela_s"})
    score = _bloco(dados, "score", {"saturacao_imobilidade_min"})
    imobilidade = _imobilidade(dados)
    return ConfigMovimentacao(
        versao=str(_get(dados, "versao", "raiz")),
        fuso=_fuso(_get(dados, "fuso", "raiz")),
        noite=Noite(
            inicio=_hora(_get(noite, "inicio", "noite"), "noite.inicio"),
            fim=_hora(_get(noite, "fim", "noite"), "noite.fim"),
        ),
        lacuna_max_min=_numero(_get(dados, "lacuna_max_min", "raiz"), "lacuna_max_min"),
        imobilidade=imobilidade,
        saida_leito=SaidaLeito(
            severidade_dia=_severidade(
                _get(saida, "severidade_dia", "saida_leito"), "saida_leito.severidade_dia"
            ),
            severidade_noite=_severidade(
                _get(saida, "severidade_noite", "saida_leito"), "saida_leito.severidade_noite"
            ),
        ),
        queda=Queda(
            severidade=_severidade(_get(queda, "severidade", "queda"), "queda.severidade"),
            fusao_janela_s=_numero(_get(queda, "fusao_janela_s", "queda"), "queda.fusao_janela_s"),
        ),
        baseline=_baseline(dados),
        saturacao_imobilidade_min=_numero(
            _get(score, "saturacao_imobilidade_min", "score"),
            "score.saturacao_imobilidade_min",
            minimo=imobilidade.limiar_min,
        ),
        video=_mapa(dados.get("video", {}), "video"),
        fonte=fonte,
    )


def carregar_config(caminho: str | Path = CAMINHO_PADRAO) -> ConfigMovimentacao:
    caminho = Path(caminho)
    if not caminho.is_file():
        raise ErroDeConfiguracao(f"configuração da US17 não encontrada: {caminho}")
    with caminho.open(encoding="utf-8") as fh:
        dados = yaml.safe_load(fh)
    if not isinstance(dados, Mapping):
        raise ErroDeConfiguracao(f"{caminho}: o arquivo deve conter um mapeamento YAML")
    return config_de_dict(dados, fonte=caminho)


__all__ = [
    "CAMINHO_PADRAO",
    "Baseline",
    "ConfigMovimentacao",
    "ErroDeConfiguracao",
    "Imobilidade",
    "Noite",
    "Queda",
    "SaidaLeito",
    "carregar_config",
    "config_de_dict",
]
