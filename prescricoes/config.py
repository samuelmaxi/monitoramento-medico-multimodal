"""Leitura do ``config/us16_prescricoes.yaml``: limiares e lista ISMP.

Como na US08, chave obrigatória ausente, valor fora do domínio ou chave
desconhecida geram :class:`ErroDeConfiguracao` na carga, com o caminho da chave.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from contratos import Severidade

from .normalizacao import TIPOS_DE_FREQUENCIA

CAMINHO_PADRAO = Path("config/us16_prescricoes.yaml")


class ErroDeConfiguracao(ValueError):
    """Configuração da US16 ausente ou inválida."""


@dataclass(frozen=True)
class Geral:
    nao_terapeuticos: tuple[re.Pattern[str], ...]
    vias_sistemicas: frozenset[str]

    def terapeutico(self, nome: str) -> bool:
        return not any(p.search(nome) for p in self.nao_terapeuticos)


@dataclass(frozen=True)
class RegraDose:
    variacao_min_pct: float
    janela_h: float
    frequencias_comparaveis: frozenset[str]
    saturacao_pct: float
    severidade: Severidade
    severidade_alta_vigilancia: Severidade


@dataclass(frozen=True)
class RegraDuplicidade:
    sobreposicao_min_h: float
    frequencias_ignoradas: frozenset[str]
    principios_permitidos: frozenset[str]
    ignorar_resgate_mesmo_produto: bool
    severidade: Severidade
    severidade_alta_vigilancia: Severidade


@dataclass(frozen=True)
class RegraSuspensao:
    classes: tuple[str, ...]
    uso_continuo_min_h: float
    sem_retorno_h: float
    desmame_reducao_min_pct: float
    troca_na_classe_conta: bool
    severidade: Severidade


@dataclass(frozen=True)
class ClasseAltaVigilancia:
    id: str
    principios: frozenset[str]
    vias: frozenset[str] | None
    """Grupos de via (``IV``, ``enteral``...); ``None`` = qualquer via."""
    apresentacao: re.Pattern[str] | None
    """Regex em ``prod_strength`` (ex.: KCl concentrado ``2 mEq/mL``)."""
    nome: re.Pattern[str] | None
    """Regex no nome do medicamento (ex.: ``3%`` do NaCl hipertônico)."""
    severidade: Severidade

    def inclui(self, principios: tuple[str, ...], via: str, nome: str, apresentacao: str) -> bool:
        if not self.principios.intersection(principios):
            return False
        if self.vias is not None and via not in self.vias:
            return False
        if self.apresentacao is not None and not self.apresentacao.search(apresentacao):
            return False
        return self.nome is None or bool(self.nome.search(nome))


@dataclass(frozen=True)
class AltaVigilancia:
    lista: str
    classes: tuple[ClasseAltaVigilancia, ...]

    def classe(
        self, principios: tuple[str, ...], via: str, nome: str, apresentacao: str = ""
    ) -> ClasseAltaVigilancia | None:
        for classe in self.classes:
            if classe.inclui(principios, via, nome, apresentacao):
                return classe
        return None


@dataclass(frozen=True)
class ConfigPrescricoes:
    versao: str
    geral: Geral
    dose: RegraDose
    duplicidade: RegraDuplicidade
    suspensao: RegraSuspensao
    alta_vigilancia: AltaVigilancia
    classes_terapeuticas: Mapping[str, frozenset[str]]
    fonte: Path | None = None

    def classe_terapeutica(self, principio: str) -> str | None:
        for classe, principios in self.classes_terapeuticas.items():
            if principio in principios:
                return classe
        return None


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


def _sem_chaves_extras(dados: Mapping[str, Any], permitidas: set[str], caminho: str) -> None:
    extras = set(dados) - permitidas
    if extras:
        raise ErroDeConfiguracao(f"chaves desconhecidas em '{caminho}': {sorted(extras)}")


def _numero(valor: Any, caminho: str, *, positivo: bool = True) -> float:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ErroDeConfiguracao(f"'{caminho}' precisa ser um número (recebido: {valor!r})")
    if positivo and valor <= 0:
        raise ErroDeConfiguracao(f"'{caminho}' precisa ser positivo (recebido: {valor!r})")
    return float(valor)


def _severidade(valor: Any, caminho: str) -> Severidade:
    try:
        return Severidade(valor)
    except ValueError:
        validas = ", ".join(s.value for s in Severidade)
        raise ErroDeConfiguracao(f"'{caminho}' = {valor!r}; use {validas}") from None


def _lista(valor: Any, caminho: str, *, vazia: bool = False) -> tuple[str, ...]:
    if not isinstance(valor, list) or (not vazia and not valor):
        raise ErroDeConfiguracao(f"'{caminho}' precisa ser uma lista não vazia")
    return tuple(str(v) for v in valor)


def _regex(valor: Any, caminho: str) -> re.Pattern[str]:
    try:
        return re.compile(str(valor), re.IGNORECASE)
    except re.error as erro:
        raise ErroDeConfiguracao(f"'{caminho}': regex inválida ({erro})") from None


def _frequencias(valor: Any, caminho: str) -> frozenset[str]:
    tipos = frozenset(_lista(valor, caminho, vazia=True))
    desconhecidos = tipos - TIPOS_DE_FREQUENCIA
    if desconhecidos:
        raise ErroDeConfiguracao(
            f"'{caminho}': {sorted(desconhecidos)} não são tipos de frequência. "
            f"Válidos: {sorted(TIPOS_DE_FREQUENCIA)}"
        )
    return tipos


def _geral(bruto: Any) -> Geral:
    dados = _mapa(bruto, "geral")
    _sem_chaves_extras(dados, {"nao_terapeuticos", "vias_sistemicas"}, "geral")
    return Geral(
        nao_terapeuticos=tuple(
            _regex(p, f"geral.nao_terapeuticos[{i}]")
            for i, p in enumerate(_lista(dados.get("nao_terapeuticos", []), "geral.nao_terapeuticos", vazia=True))
        ),
        vias_sistemicas=frozenset(_lista(_get(dados, "vias_sistemicas", "geral"), "geral.vias_sistemicas")),
    )


def _dose(bruto: Any) -> RegraDose:
    c = "mudanca_abrupta_dose"
    dados = _mapa(bruto, c)
    _sem_chaves_extras(
        dados,
        {"variacao_min_pct", "janela_h", "frequencias_comparaveis", "saturacao_pct",
         "severidade", "severidade_alta_vigilancia"},
        c,
    )
    variacao = _numero(_get(dados, "variacao_min_pct", c), f"{c}.variacao_min_pct")
    saturacao = _numero(_get(dados, "saturacao_pct", c), f"{c}.saturacao_pct")
    if saturacao <= variacao:
        raise ErroDeConfiguracao(f"'{c}.saturacao_pct' precisa ser maior que variacao_min_pct")
    return RegraDose(
        variacao_min_pct=variacao,
        janela_h=_numero(_get(dados, "janela_h", c), f"{c}.janela_h"),
        frequencias_comparaveis=_frequencias(
            _get(dados, "frequencias_comparaveis", c), f"{c}.frequencias_comparaveis"
        ),
        saturacao_pct=saturacao,
        severidade=_severidade(dados.get("severidade", "baixa"), f"{c}.severidade"),
        severidade_alta_vigilancia=_severidade(
            dados.get("severidade_alta_vigilancia", "media"), f"{c}.severidade_alta_vigilancia"
        ),
    )


def _duplicidade(bruto: Any) -> RegraDuplicidade:
    c = "duplicidade_terapeutica"
    dados = _mapa(bruto, c)
    _sem_chaves_extras(
        dados,
        {"sobreposicao_min_h", "frequencias_ignoradas", "principios_permitidos",
         "ignorar_resgate_mesmo_produto", "severidade", "severidade_alta_vigilancia"},
        c,
    )
    resgate = dados.get("ignorar_resgate_mesmo_produto", True)
    if not isinstance(resgate, bool):
        raise ErroDeConfiguracao(f"'{c}.ignorar_resgate_mesmo_produto' precisa ser true/false")
    return RegraDuplicidade(
        sobreposicao_min_h=_numero(_get(dados, "sobreposicao_min_h", c), f"{c}.sobreposicao_min_h"),
        frequencias_ignoradas=_frequencias(
            dados.get("frequencias_ignoradas", []), f"{c}.frequencias_ignoradas"
        ),
        principios_permitidos=frozenset(
            _lista(dados.get("principios_permitidos", []), f"{c}.principios_permitidos", vazia=True)
        ),
        ignorar_resgate_mesmo_produto=resgate,
        severidade=_severidade(dados.get("severidade", "media"), f"{c}.severidade"),
        severidade_alta_vigilancia=_severidade(
            dados.get("severidade_alta_vigilancia", "alta"), f"{c}.severidade_alta_vigilancia"
        ),
    )


def _suspensao(bruto: Any, classes_terapeuticas: Mapping[str, frozenset[str]]) -> RegraSuspensao:
    c = "suspensao_abrupta"
    dados = _mapa(bruto, c)
    _sem_chaves_extras(
        dados,
        {"classes", "uso_continuo_min_h", "sem_retorno_h", "desmame_reducao_min_pct",
         "troca_na_classe_conta", "severidade"},
        c,
    )
    classes = _lista(_get(dados, "classes", c), f"{c}.classes")
    for classe in classes:
        if classe not in classes_terapeuticas:
            raise ErroDeConfiguracao(
                f"'{c}.classes': '{classe}' não está em classes_terapeuticas"
            )
    reducao = _numero(_get(dados, "desmame_reducao_min_pct", c), f"{c}.desmame_reducao_min_pct")
    if reducao >= 100:
        raise ErroDeConfiguracao(f"'{c}.desmame_reducao_min_pct' precisa ser menor que 100")
    troca = dados.get("troca_na_classe_conta", True)
    if not isinstance(troca, bool):
        raise ErroDeConfiguracao(f"'{c}.troca_na_classe_conta' precisa ser true/false")
    return RegraSuspensao(
        classes=classes,
        uso_continuo_min_h=_numero(_get(dados, "uso_continuo_min_h", c), f"{c}.uso_continuo_min_h"),
        sem_retorno_h=_numero(_get(dados, "sem_retorno_h", c), f"{c}.sem_retorno_h"),
        desmame_reducao_min_pct=reducao,
        troca_na_classe_conta=troca,
        severidade=_severidade(dados.get("severidade", "media"), f"{c}.severidade"),
    )


def _alta_vigilancia(bruto: Any) -> AltaVigilancia:
    c = "alta_vigilancia"
    dados = _mapa(bruto, c)
    _sem_chaves_extras(dados, {"lista", "classes"}, c)
    classes = []
    for classe_id, regra in _mapa(_get(dados, "classes", c), f"{c}.classes").items():
        aqui = f"{c}.classes.{classe_id}"
        regra = _mapa(regra, aqui)
        _sem_chaves_extras(regra, {"principios", "vias", "apresentacao", "nome", "severidade"}, aqui)
        classes.append(
            ClasseAltaVigilancia(
                id=str(classe_id),
                principios=frozenset(_lista(_get(regra, "principios", aqui), f"{aqui}.principios")),
                vias=frozenset(_lista(regra["vias"], f"{aqui}.vias")) if "vias" in regra else None,
                apresentacao=_regex(regra["apresentacao"], f"{aqui}.apresentacao")
                if "apresentacao" in regra
                else None,
                nome=_regex(regra["nome"], f"{aqui}.nome") if "nome" in regra else None,
                severidade=_severidade(regra.get("severidade", "media"), f"{aqui}.severidade"),
            )
        )
    if not classes:
        raise ErroDeConfiguracao(f"'{c}.classes' está vazio")
    return AltaVigilancia(lista=str(_get(dados, "lista", c)), classes=tuple(classes))


def config_de_dict(dados: Any, *, fonte: Path | None = None) -> ConfigPrescricoes:
    dados = _mapa(dados, "<raiz>")
    _sem_chaves_extras(
        dados,
        {"versao", "geral", "mudanca_abrupta_dose", "duplicidade_terapeutica",
         "suspensao_abrupta", "alta_vigilancia", "classes_terapeuticas"},
        "<raiz>",
    )
    versao = _get(dados, "versao", "<raiz>")
    if not isinstance(versao, str) or not versao.strip():
        raise ErroDeConfiguracao("'versao' precisa ser texto, ex. \"1.0.0\"")
    classes_terapeuticas = {
        str(classe): frozenset(_lista(principios, f"classes_terapeuticas.{classe}"))
        for classe, principios in _mapa(
            _get(dados, "classes_terapeuticas", "<raiz>"), "classes_terapeuticas"
        ).items()
    }
    return ConfigPrescricoes(
        versao=versao,
        geral=_geral(_get(dados, "geral", "<raiz>")),
        dose=_dose(_get(dados, "mudanca_abrupta_dose", "<raiz>")),
        duplicidade=_duplicidade(_get(dados, "duplicidade_terapeutica", "<raiz>")),
        suspensao=_suspensao(_get(dados, "suspensao_abrupta", "<raiz>"), classes_terapeuticas),
        alta_vigilancia=_alta_vigilancia(_get(dados, "alta_vigilancia", "<raiz>")),
        classes_terapeuticas=classes_terapeuticas,
        fonte=fonte,
    )


def carregar_config(caminho: str | Path = CAMINHO_PADRAO) -> ConfigPrescricoes:
    caminho = Path(caminho)
    if not caminho.is_file():
        raise ErroDeConfiguracao(f"arquivo de configuração não encontrado: {caminho}")
    with caminho.open(encoding="utf-8") as arquivo:
        dados = yaml.safe_load(arquivo)
    return config_de_dict(dados, fonte=caminho)
