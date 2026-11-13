"""Leitura do ``config/us09_relatorio.yaml``: critérios de apresentação do relatório.

Como na US08, chave ausente, desconhecida ou fora do domínio gera :class:`ErroDeConfiguracao`
com o caminho da chave, na carga e não no meio da geração do relatório.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CAMINHO_PADRAO = Path("config/us09_relatorio.yaml")


class ErroDeConfiguracao(ValueError):
    """Configuração da US09 ausente ou inválida."""


@dataclass(frozen=True)
class ConfigConfianca:
    minima: float
    cobertura_minima_pct: float


@dataclass(frozen=True)
class ConfigEvidencia:
    largura_html_px: int
    qualidade_jpeg: int


@dataclass(frozen=True)
class ConfigRelatorio:
    versao: str
    confianca: ConfigConfianca
    evidencia: ConfigEvidencia
    fonte: Path | None = None


def _mapa(valor: Any, caminho: str) -> Mapping[str, Any]:
    if not isinstance(valor, Mapping):
        raise ErroDeConfiguracao(f"'{caminho}' precisa ser um mapeamento")
    return valor


def _get(dados: Mapping[str, Any], chave: str, caminho: str) -> Any:
    if chave not in dados:
        raise ErroDeConfiguracao(f"chave obrigatória ausente: '{caminho}.{chave}'")
    return dados[chave]


def _sem_extras(dados: Mapping[str, Any], permitidas: set[str], caminho: str) -> None:
    extras = set(dados) - permitidas
    if extras:
        raise ErroDeConfiguracao(f"chaves desconhecidas em '{caminho}': {sorted(extras)}")


def _numero(valor: Any, caminho: str, minimo: float, maximo: float, *, inteiro: bool = False):
    tipo = int if inteiro else (int, float)
    if isinstance(valor, bool) or not isinstance(valor, tipo):
        esperado = "um inteiro" if inteiro else "um número"
        raise ErroDeConfiguracao(f"'{caminho}' precisa ser {esperado} (recebido: {valor!r})")
    if not minimo <= valor <= maximo:
        raise ErroDeConfiguracao(
            f"'{caminho}' precisa estar entre {minimo:g} e {maximo:g} (recebido: {valor!r})"
        )
    return valor if inteiro else float(valor)


def config_de_dict(dados: Mapping[str, Any], fonte: Path | None = None) -> ConfigRelatorio:
    dados = _mapa(dados, "raiz")
    _sem_extras(dados, {"versao", "confianca", "evidencia"}, "raiz")
    confianca = _mapa(_get(dados, "confianca", "raiz"), "confianca")
    _sem_extras(confianca, {"minima", "cobertura_minima_pct"}, "confianca")
    evidencia = _mapa(_get(dados, "evidencia", "raiz"), "evidencia")
    _sem_extras(evidencia, {"largura_html_px", "qualidade_jpeg"}, "evidencia")
    versao = str(_get(dados, "versao", "raiz")).strip()
    if not versao:
        raise ErroDeConfiguracao("'versao' não pode ser vazia")
    return ConfigRelatorio(
        versao=versao,
        confianca=ConfigConfianca(
            minima=_numero(_get(confianca, "minima", "confianca"), "confianca.minima", 0, 1),
            cobertura_minima_pct=_numero(
                _get(confianca, "cobertura_minima_pct", "confianca"),
                "confianca.cobertura_minima_pct",
                0,
                100,
            ),
        ),
        evidencia=ConfigEvidencia(
            largura_html_px=_numero(
                _get(evidencia, "largura_html_px", "evidencia"),
                "evidencia.largura_html_px",
                64,
                4096,
                inteiro=True,
            ),
            qualidade_jpeg=_numero(
                _get(evidencia, "qualidade_jpeg", "evidencia"),
                "evidencia.qualidade_jpeg",
                1,
                100,
                inteiro=True,
            ),
        ),
        fonte=fonte,
    )


def carregar_config(caminho: str | Path = CAMINHO_PADRAO) -> ConfigRelatorio:
    caminho = Path(caminho)
    if not caminho.is_file():
        raise ErroDeConfiguracao(f"configuração da US09 não encontrada: {caminho}")
    with caminho.open(encoding="utf-8") as arquivo:
        dados = yaml.safe_load(arquivo)
    if not isinstance(dados, Mapping):
        raise ErroDeConfiguracao(f"{caminho}: o arquivo deve conter um mapeamento YAML")
    return config_de_dict(dados, fonte=caminho)


__all__ = [
    "CAMINHO_PADRAO",
    "ConfigConfianca",
    "ConfigEvidencia",
    "ConfigRelatorio",
    "ErroDeConfiguracao",
    "carregar_config",
    "config_de_dict",
]
