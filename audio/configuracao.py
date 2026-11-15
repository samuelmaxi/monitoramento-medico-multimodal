"""Leitura estrita de YAML, compartilhada por ``audio.ingestao`` (US10) e ``audio.vocal``.

Como nos demais módulos (``prescricoes``, ``movimentacao``), não existem valores padrão
escondidos no código: chave obrigatória ausente, valor fora do domínio ou chave
desconhecida geram :class:`ErroDeConfiguracao` na carga, com o caminho da chave.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml


class ErroDeConfiguracao(ValueError):
    """Configuração de áudio ausente ou inválida."""


def ler_yaml(caminho: str | Path) -> Any:
    caminho = Path(caminho)
    if not caminho.is_file():
        raise ErroDeConfiguracao(f"arquivo de configuração não encontrado: {caminho}")
    try:
        with caminho.open(encoding="utf-8") as arquivo:
            return yaml.safe_load(arquivo)
    except yaml.YAMLError as erro:
        raise ErroDeConfiguracao(f"YAML inválido em {caminho}: {erro}") from None


def mapa(valor: Any, caminho: str) -> Mapping[str, Any]:
    if not isinstance(valor, Mapping):
        raise ErroDeConfiguracao(f"'{caminho}' precisa ser um mapeamento")
    return valor


def obter(dados: Mapping[str, Any], chave: str, caminho: str) -> Any:
    if chave not in dados:
        raise ErroDeConfiguracao(f"chave obrigatória ausente: '{caminho}.{chave}'")
    return dados[chave]


def sem_chaves_extras(dados: Mapping[str, Any], permitidas: set[str], caminho: str) -> None:
    extras = set(dados) - permitidas
    if extras:
        raise ErroDeConfiguracao(f"chaves desconhecidas em '{caminho}': {sorted(extras)}")


def numero(
    valor: Any,
    caminho: str,
    *,
    minimo: float | None = None,
    maximo: float | None = None,
    minimo_exclusivo: bool = False,
) -> float:
    """Número finito em ``[minimo, maximo]``; ``minimo_exclusivo`` abre o piso do intervalo."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
        raise ErroDeConfiguracao(f"'{caminho}' precisa ser um número finito (recebido: {valor!r})")
    if minimo is not None and (valor < minimo or (minimo_exclusivo and valor == minimo)):
        sinal = ">" if minimo_exclusivo else ">="
        raise ErroDeConfiguracao(f"'{caminho}' precisa ser {sinal} {minimo} (recebido: {valor!r})")
    if maximo is not None and valor > maximo:
        raise ErroDeConfiguracao(f"'{caminho}' precisa ser <= {maximo} (recebido: {valor!r})")
    return float(valor)


def inteiro(valor: Any, caminho: str, *, minimo: int | None = None) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise ErroDeConfiguracao(f"'{caminho}' precisa ser um inteiro (recebido: {valor!r})")
    if minimo is not None and valor < minimo:
        raise ErroDeConfiguracao(f"'{caminho}' precisa ser >= {minimo} (recebido: {valor!r})")
    return valor


def booleano(valor: Any, caminho: str) -> bool:
    if not isinstance(valor, bool):
        raise ErroDeConfiguracao(f"'{caminho}' precisa ser true/false (recebido: {valor!r})")
    return valor


def texto(valor: Any, caminho: str) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise ErroDeConfiguracao(f"'{caminho}' precisa ser um texto não vazio")
    return valor.strip()


def lista_de_textos(valor: Any, caminho: str, *, vazia: bool = False) -> tuple[str, ...]:
    if not isinstance(valor, list) or (not vazia and not valor):
        raise ErroDeConfiguracao(f"'{caminho}' precisa ser uma lista não vazia")
    return tuple(texto(item, f"{caminho}[{i}]") for i, item in enumerate(valor))
