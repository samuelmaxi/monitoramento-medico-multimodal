"""Leitura do ``config/us18_fusao.yaml``: janela, pesos, concordância e níveis.

Como na US16, chave obrigatória ausente, valor fora do domínio ou chave
desconhecida geram :class:`ErroDeConfiguracao` na carga, com o caminho da chave.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml

from contratos import TIPOS_DE_EVENTO, Modalidade, Severidade

CAMINHO_PADRAO = Path("config/us18_fusao.yaml")

MODALIDADES_FUNDIDAS = tuple(m for m in Modalidade if m is not Modalidade.FUSAO)


class ErroDeConfiguracao(ValueError):
    """Configuração da US18 ausente ou inválida."""


@dataclass(frozen=True)
class MembroEixo:
    """Achados de uma modalidade que contam para um eixo clínico."""

    modalidade: Modalidade
    tipos: frozenset[str]
    termos: frozenset[str] | None = None
    """Filtro em ``features.termo`` (texto, US13). ``None`` = qualquer termo."""


@dataclass(frozen=True)
class ConfigFusao:
    versao: str
    janela: timedelta
    meia_vida: timedelta | None
    valor_severidade: Mapping[Severidade, float]
    influencia_score: float
    pesos: Mapping[Modalidade, float]
    pesos_por_tipo: Mapping[tuple[Modalidade, str], float]
    fator_concordancia: float
    eixos: Mapping[str, tuple[MembroEixo, ...]]
    nivel_medio: float
    nivel_alto: float
    alertar_com_uma_modalidade: bool
    variacao_minima: float
    origem: str = field(default=str(CAMINHO_PADRAO), compare=False)

    def peso(self, modalidade: Modalidade, tipo: str) -> float:
        return self.pesos_por_tipo.get((modalidade, tipo), self.pesos[modalidade])

    def com_janela(self, minutos: float) -> ConfigFusao:
        """Cópia com outra janela (parâmetro da API e da CLI)."""
        if minutos <= 0:
            raise ErroDeConfiguracao("janela_min precisa ser > 0")
        return replace(self, janela=timedelta(minutes=minutos))

    def limiares(self) -> dict[str, float | str]:
        """O que vai em ``evidence.thresholds`` do ``risco_multimodal``."""
        return {
            "versao_config": self.versao,
            "janela_min": _minutos(self.janela),
            "meia_vida_min": _minutos(self.meia_vida) if self.meia_vida else 0,
            "risco_medio": self.nivel_medio,
            "risco_alto": self.nivel_alto,
            "fator_concordancia": self.fator_concordancia,
            **{f"peso_{m.value}": p for m, p in self.pesos.items()},
        }


def carregar(caminho: str | Path = CAMINHO_PADRAO) -> ConfigFusao:
    caminho = Path(caminho)
    try:
        bruto = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ErroDeConfiguracao(f"arquivo de configuração não encontrado: {caminho}") from exc
    return de_dict(bruto, origem=str(caminho))


def de_dict(dados: Mapping[str, Any], *, origem: str = "<dict>") -> ConfigFusao:
    _sem_extras(dados, {"versao", "janela_min", "meia_vida_min", "valor_severidade",
                        "influencia_score", "pesos", "pesos_por_tipo", "concordancia",
                        "niveis", "emissao"}, "")

    janela_min = _numero(dados, "janela_min", minimo=0, exclusivo=True)
    meia_vida_min = _numero(dados, "meia_vida_min", minimo=0)

    sev_bruto = _mapa(dados, "valor_severidade")
    _sem_extras(sev_bruto, {s.value for s in Severidade}, "valor_severidade.")
    valor_severidade = {
        s: _numero(sev_bruto, s.value, minimo=0, maximo=1, prefixo="valor_severidade.")
        for s in Severidade
    }

    pesos_bruto = _mapa(dados, "pesos")
    _sem_extras(pesos_bruto, {m.value for m in MODALIDADES_FUNDIDAS}, "pesos.")
    pesos = {
        m: _numero(pesos_bruto, m.value, minimo=0, maximo=1, prefixo="pesos.")
        for m in MODALIDADES_FUNDIDAS
    }

    pesos_por_tipo: dict[tuple[Modalidade, str], float] = {}
    for chave, valor in (dados.get("pesos_por_tipo") or {}).items():
        modalidade, _, tipo = str(chave).partition("/")
        mod = _modalidade(modalidade, f"pesos_por_tipo.{chave}")
        _tipo(mod, tipo, f"pesos_por_tipo.{chave}")
        pesos_por_tipo[(mod, tipo)] = _numero(
            {"v": valor}, "v", minimo=0, maximo=1, prefixo=f"pesos_por_tipo.{chave}:"
        )

    conc = _mapa(dados, "concordancia")
    _sem_extras(conc, {"fator", "eixos"}, "concordancia.")
    fator = _numero(conc, "fator", minimo=0, maximo=1, prefixo="concordancia.")
    eixos = {
        str(nome): _eixo(str(nome), membros)
        for nome, membros in (conc.get("eixos") or {}).items()
    }

    niveis = _mapa(dados, "niveis")
    _sem_extras(niveis, {"medio", "alto"}, "niveis.")
    medio = _numero(niveis, "medio", minimo=0, maximo=1, prefixo="niveis.")
    alto = _numero(niveis, "alto", minimo=0, maximo=1, prefixo="niveis.")
    if not medio < alto:
        raise ErroDeConfiguracao("niveis.medio precisa ser menor que niveis.alto")

    emissao = _mapa(dados, "emissao")
    _sem_extras(emissao, {"alertar_com_uma_modalidade", "variacao_minima"}, "emissao.")
    alertar_uma = emissao.get("alertar_com_uma_modalidade")
    if not isinstance(alertar_uma, bool):
        raise ErroDeConfiguracao("emissao.alertar_com_uma_modalidade precisa ser true ou false")

    versao = dados.get("versao")
    if not isinstance(versao, str) or not versao:
        raise ErroDeConfiguracao("versao precisa ser um texto, ex. \"1.0.0\"")

    return ConfigFusao(
        versao=versao,
        janela=timedelta(minutes=janela_min),
        meia_vida=timedelta(minutes=meia_vida_min) if meia_vida_min else None,
        valor_severidade=valor_severidade,
        influencia_score=_numero(dados, "influencia_score", minimo=0, maximo=1),
        pesos=pesos,
        pesos_por_tipo=pesos_por_tipo,
        fator_concordancia=fator,
        eixos=eixos,
        nivel_medio=medio,
        nivel_alto=alto,
        alertar_com_uma_modalidade=alertar_uma,
        variacao_minima=_numero(emissao, "variacao_minima", minimo=0, maximo=1, prefixo="emissao."),
        origem=origem,
    )


# --------------------------------------------------------------------------- #
# Validação
# --------------------------------------------------------------------------- #

def _eixo(nome: str, membros: Any) -> tuple[MembroEixo, ...]:
    caminho = f"concordancia.eixos.{nome}"
    if not isinstance(membros, list) or len(membros) < 2:
        raise ErroDeConfiguracao(f"{caminho} precisa de ao menos 2 modalidades")
    resultado = []
    vistas: set[Modalidade] = set()
    for i, membro in enumerate(membros):
        local = f"{caminho}[{i}]"
        if not isinstance(membro, Mapping):
            raise ErroDeConfiguracao(f"{local} precisa ser um objeto")
        _sem_extras(membro, {"modalidade", "tipos", "termos"}, f"{local}.")
        mod = _modalidade(membro.get("modalidade"), f"{local}.modalidade")
        if mod in vistas:
            raise ErroDeConfiguracao(f"{local}: modalidade {mod.value} repetida no eixo")
        vistas.add(mod)
        tipos = membro.get("tipos")
        if not isinstance(tipos, list) or not tipos:
            raise ErroDeConfiguracao(f"{local}.tipos precisa ser uma lista não vazia")
        for tipo in tipos:
            _tipo(mod, str(tipo), f"{local}.tipos")
        termos = membro.get("termos")
        if termos is not None and (not isinstance(termos, list) or not termos):
            raise ErroDeConfiguracao(f"{local}.termos precisa ser uma lista não vazia")
        resultado.append(MembroEixo(
            mod,
            frozenset(str(t) for t in tipos),
            frozenset(str(t).casefold() for t in termos) if termos else None,
        ))
    return tuple(resultado)


def _modalidade(valor: Any, caminho: str) -> Modalidade:
    try:
        mod = Modalidade(valor)
    except ValueError as exc:
        raise ErroDeConfiguracao(f"{caminho}: modalidade desconhecida {valor!r}") from exc
    if mod is Modalidade.FUSAO:
        raise ErroDeConfiguracao(f"{caminho}: a fusão não funde a si mesma")
    return mod


def _tipo(mod: Modalidade, tipo: str, caminho: str) -> None:
    if tipo not in TIPOS_DE_EVENTO[mod]:
        raise ErroDeConfiguracao(
            f"{caminho}: tipo {tipo!r} não existe na modalidade {mod.value} (contratos/catalogo.py)"
        )


def _mapa(dados: Mapping[str, Any], chave: str) -> Mapping[str, Any]:
    valor = dados.get(chave)
    if not isinstance(valor, Mapping):
        raise ErroDeConfiguracao(f"{chave} ausente ou não é um objeto")
    return valor


def _numero(
    dados: Mapping[str, Any],
    chave: str,
    *,
    minimo: float | None = None,
    maximo: float | None = None,
    exclusivo: bool = False,
    prefixo: str = "",
) -> float:
    valor = dados.get(chave)
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ErroDeConfiguracao(f"{prefixo}{chave} ausente ou não numérico")
    if minimo is not None and (valor <= minimo if exclusivo else valor < minimo):
        raise ErroDeConfiguracao(f"{prefixo}{chave} = {valor} abaixo do mínimo {minimo}")
    if maximo is not None and valor > maximo:
        raise ErroDeConfiguracao(f"{prefixo}{chave} = {valor} acima do máximo {maximo}")
    return float(valor)


def _sem_extras(dados: Mapping[str, Any], permitidas: set[str], prefixo: str) -> None:
    extras = set(map(str, dados)) - permitidas
    if extras:
        nomes = ", ".join(prefixo + e for e in sorted(extras))
        raise ErroDeConfiguracao(f"chave(s) desconhecida(s): {nomes}")


def _minutos(intervalo: timedelta) -> float:
    minutos = intervalo.total_seconds() / 60
    return int(minutos) if minutos.is_integer() else minutos
