"""Leitura do ``audio/ingestao/config.yaml`` (US10)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from audio.configuracao import (
    ErroDeConfiguracao,
    ler_yaml,
    lista_de_textos,
    mapa,
    numero,
    obter,
    sem_chaves_extras,
    texto,
)

__all__ = [
    "CAMINHO_PADRAO",
    "ConfigIngestao",
    "CopiaFeaturesCfg",
    "ErroDeConfiguracao",
    "Proveniencia",
    "SilencioCfg",
    "Validacao",
    "carregar_config",
    "config_de_dict",
]

CAMINHO_PADRAO = Path("audio/ingestao/config.yaml")


@dataclass(frozen=True)
class Validacao:
    duracao_minima_s: float
    duracao_maxima_s: float
    extensoes: frozenset[str]
    decodificador_nativo: frozenset[str]
    ffmpeg_timeout_s: float
    pico_minimo: float


@dataclass(frozen=True)
class SilencioCfg:
    limiar_relativo_db: float
    duracao_longa_s: float
    manter_s: float
    janela_ms: float


@dataclass(frozen=True)
class CopiaFeaturesCfg:
    pico_alvo: float
    ganho_maximo: float
    silencio: SilencioCfg


@dataclass(frozen=True)
class Proveniencia:
    origem_padrao: str
    licenca_padrao: str


@dataclass(frozen=True)
class ConfigIngestao:
    versao: str
    taxa_amostragem_hz: int
    validacao: Validacao
    copia_features: CopiaFeaturesCfg
    proveniencia: Proveniencia
    fonte: Path | None = None


def _extensoes(valor: Any, caminho: str) -> frozenset[str]:
    return frozenset(e.lower().lstrip(".") for e in lista_de_textos(valor, caminho))


def _validacao(bruto: Any) -> Validacao:
    c = "validacao"
    dados = mapa(bruto, c)
    sem_chaves_extras(
        dados,
        {"duracao_minima_s", "duracao_maxima_s", "extensoes", "decodificador_nativo",
         "ffmpeg_timeout_s", "pico_minimo"},
        c,
    )
    minima = numero(obter(dados, "duracao_minima_s", c), f"{c}.duracao_minima_s", minimo=0,
                    minimo_exclusivo=True)
    maxima = numero(obter(dados, "duracao_maxima_s", c), f"{c}.duracao_maxima_s", minimo=0,
                    minimo_exclusivo=True)
    if maxima <= minima:
        raise ErroDeConfiguracao(f"'{c}.duracao_maxima_s' precisa ser maior que duracao_minima_s")
    extensoes = _extensoes(obter(dados, "extensoes", c), f"{c}.extensoes")
    nativo = _extensoes(obter(dados, "decodificador_nativo", c), f"{c}.decodificador_nativo")
    if not nativo <= extensoes:
        fora = sorted(nativo - extensoes)
        raise ErroDeConfiguracao(
            f"'{c}.decodificador_nativo' tem extensões fora de 'extensoes': {fora}"
        )
    return Validacao(
        duracao_minima_s=minima,
        duracao_maxima_s=maxima,
        extensoes=extensoes,
        decodificador_nativo=nativo,
        ffmpeg_timeout_s=numero(obter(dados, "ffmpeg_timeout_s", c), f"{c}.ffmpeg_timeout_s",
                                minimo=0, minimo_exclusivo=True),
        pico_minimo=numero(obter(dados, "pico_minimo", c), f"{c}.pico_minimo", minimo=0,
                           maximo=1, minimo_exclusivo=True),
    )


def _silencio(bruto: Any) -> SilencioCfg:
    c = "copia_features.silencio"
    dados = mapa(bruto, c)
    sem_chaves_extras(dados, {"limiar_relativo_db", "duracao_longa_s", "manter_s", "janela_ms"}, c)
    longa = numero(obter(dados, "duracao_longa_s", c), f"{c}.duracao_longa_s", minimo=0,
                   minimo_exclusivo=True)
    manter = numero(obter(dados, "manter_s", c), f"{c}.manter_s", minimo=0)
    if manter >= longa:
        raise ErroDeConfiguracao(f"'{c}.manter_s' precisa ser menor que duracao_longa_s")
    return SilencioCfg(
        limiar_relativo_db=numero(obter(dados, "limiar_relativo_db", c),
                                  f"{c}.limiar_relativo_db", maximo=-1),
        duracao_longa_s=longa,
        manter_s=manter,
        janela_ms=numero(obter(dados, "janela_ms", c), f"{c}.janela_ms", minimo=1),
    )


def _copia_features(bruto: Any) -> CopiaFeaturesCfg:
    c = "copia_features"
    dados = mapa(bruto, c)
    sem_chaves_extras(dados, {"pico_alvo", "ganho_maximo", "silencio"}, c)
    return CopiaFeaturesCfg(
        pico_alvo=numero(obter(dados, "pico_alvo", c), f"{c}.pico_alvo", minimo=0, maximo=1,
                         minimo_exclusivo=True),
        ganho_maximo=numero(obter(dados, "ganho_maximo", c), f"{c}.ganho_maximo", minimo=1),
        silencio=_silencio(obter(dados, "silencio", c)),
    )


def _proveniencia(bruto: Any) -> Proveniencia:
    c = "proveniencia"
    dados = mapa(bruto, c)
    sem_chaves_extras(dados, {"origem_padrao", "licenca_padrao"}, c)
    return Proveniencia(
        origem_padrao=texto(obter(dados, "origem_padrao", c), f"{c}.origem_padrao"),
        licenca_padrao=texto(obter(dados, "licenca_padrao", c), f"{c}.licenca_padrao"),
    )


def config_de_dict(dados: Any, *, fonte: Path | None = None) -> ConfigIngestao:
    dados = mapa(dados, "<raiz>")
    sem_chaves_extras(
        dados,
        {"versao", "saida", "validacao", "copia_features", "proveniencia"},
        "<raiz>",
    )
    saida = mapa(obter(dados, "saida", "<raiz>"), "saida")
    sem_chaves_extras(saida, {"taxa_amostragem_hz"}, "saida")
    taxa = numero(obter(saida, "taxa_amostragem_hz", "saida"), "saida.taxa_amostragem_hz",
                  minimo=8000, maximo=48000)
    if taxa != int(taxa):
        raise ErroDeConfiguracao("'saida.taxa_amostragem_hz' precisa ser inteiro")
    return ConfigIngestao(
        versao=texto(obter(dados, "versao", "<raiz>"), "versao"),
        taxa_amostragem_hz=int(taxa),
        validacao=_validacao(obter(dados, "validacao", "<raiz>")),
        copia_features=_copia_features(obter(dados, "copia_features", "<raiz>")),
        proveniencia=_proveniencia(obter(dados, "proveniencia", "<raiz>")),
        fonte=fonte,
    )


def carregar_config(caminho: str | Path = CAMINHO_PADRAO) -> ConfigIngestao:
    caminho = Path(caminho)
    return config_de_dict(ler_yaml(caminho), fonte=caminho)
