"""Leitura do ``config/us08_padroes.yaml``: padrão esperado por perfil e limiares.

Uma chave obrigatória ausente ou um valor fora do domínio gera
:class:`ErroDeConfiguracao` com o caminho da chave, na carga e não no meio da
análise. Chaves desconhecidas também são recusadas: um erro de digitação
("janela" em vez de "janela_s") faria a regra rodar com o padrão sem ninguém
perceber.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from contratos import Severidade
from video.pose.angles import ASYMMETRIES, JOINT_ANGLES

CAMINHO_PADRAO = Path("config/us08_padroes.yaml")

TIPOS_DE_VIDEO = frozenset({"fisioterapia", "leito", "cirurgia"})
SENTIDOS = frozenset({"sobe", "desce"})


class ErroDeConfiguracao(ValueError):
    """Configuração da US08 ausente ou inválida."""


@dataclass(frozen=True)
class Score:
    saturacao_graus: float
    saturacao_torsos: float


@dataclass(frozen=True)
class Geral:
    janela_minima_s: float
    lacuna_max_s: float
    fracao_permanencia: float
    score: Score
    cobertura_minima_pct: float


@dataclass(frozen=True)
class Faixa:
    """Ângulo por quadro dentro de ``[min, max]`` (um dos lados pode ser aberto)."""

    coluna: str
    min: float | None
    max: float | None
    janela_s: float
    severidade: Severidade


@dataclass(frozen=True)
class Referencia:
    """Padrão do próprio paciente, tirado das primeiras repetições da sessão.

    As ``repeticoes`` iniciais (supervisionadas pelo fisioterapeuta) viram a
    referência; cada repetição seguinte é comparada a ela, ângulo a ângulo, na
    fase de pico. Passar da ``tolerancia`` (graus) em qualquer ângulo é desvio.
    """

    repeticoes: int
    tolerancias: Mapping[str, float]
    fase_pico_graus: float


@dataclass(frozen=True)
class RegraRepeticoes:
    """Separação das repetições, número esperado e o que cada uma precisa cumprir.

    ``sentido="sobe"``: a repetição é o trecho com o ângulo acima de ``repouso``
    e o pico é o máximo; ``"desce"``: trecho abaixo de ``repouso``, pico mínimo.
    ``alvo`` (absoluto) e ``referencia`` (relativa ao paciente) são opcionais,
    mas pelo menos um precisa existir.
    """

    angulo: str
    sentido: str
    repouso: float
    alvo: float | None
    esperadas: tuple[int, int] | None
    duracao_min_s: float
    severidade: Severidade
    referencia: Referencia | None = None


@dataclass(frozen=True)
class RegraSalto:
    """Mudança rápida de uma medida que se mantém (queda ou giro do tronco)."""

    limiar: float
    tempo_max_s: float
    janela_s: float
    severidade: Severidade


@dataclass(frozen=True)
class RegraArea:
    area_id: str
    classes_proibidas: frozenset[str]
    janela_s: float
    severidade: Severidade


@dataclass(frozen=True)
class Perfil:
    id: str
    tipo_video: str
    descricao: str
    faixas: tuple[Faixa, ...] = ()
    assimetrias: tuple[Faixa, ...] = ()
    repeticoes: RegraRepeticoes | None = None
    queda: RegraSalto | None = None
    mudanca_brusca: RegraSalto | None = None
    areas: tuple[RegraArea, ...] = ()

    @property
    def usa_pose(self) -> bool:
        return bool(
            self.faixas or self.assimetrias or self.repeticoes or self.queda or self.mudanca_brusca
        )


@dataclass(frozen=True)
class ConfigDesvios:
    versao: str
    geral: Geral
    perfis: Mapping[str, Perfil]
    fonte: Path | None = None

    def perfil(self, perfil_id: str) -> Perfil:
        try:
            return self.perfis[perfil_id]
        except KeyError:
            disponiveis = ", ".join(sorted(self.perfis))
            raise ErroDeConfiguracao(
                f"perfil '{perfil_id}' não existe em {self.fonte}. Disponíveis: {disponiveis}"
            ) from None


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


def _numero(valor: Any, caminho: str, *, positivo: bool = False) -> float:
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


def _janela(dados: Mapping[str, Any], caminho: str, geral: Geral) -> float:
    if "janela_s" not in dados:
        return geral.janela_minima_s
    return _numero(dados["janela_s"], f"{caminho}.janela_s", positivo=True)


def _faixas(
    bruto: Any, caminho: str, geral: Geral, colunas_validas: set[str], *, so_max: bool = False
) -> tuple[Faixa, ...]:
    faixas = []
    for coluna, regra in _mapa(bruto, caminho).items():
        aqui = f"{caminho}.{coluna}"
        if coluna not in colunas_validas:
            raise ErroDeConfiguracao(
                f"'{aqui}': coluna desconhecida. Válidas: {', '.join(sorted(colunas_validas))}"
            )
        regra = _mapa(regra, aqui)
        _sem_chaves_extras(
            regra, {"max", "janela_s", "severidade"} | (set() if so_max else {"min"}), aqui
        )
        minimo = None if regra.get("min") is None else _numero(regra["min"], f"{aqui}.min")
        maximo = None if regra.get("max") is None else _numero(regra["max"], f"{aqui}.max")
        if so_max and maximo is None:
            raise ErroDeConfiguracao(f"'{aqui}' precisa de 'max'")
        if minimo is None and maximo is None:
            raise ErroDeConfiguracao(f"'{aqui}' precisa de 'min' e/ou 'max'")
        if minimo is not None and maximo is not None and minimo >= maximo:
            raise ErroDeConfiguracao(
                f"'{aqui}': min ({minimo}) precisa ser menor que max ({maximo})"
            )
        faixas.append(
            Faixa(
                coluna=coluna,
                min=minimo,
                max=maximo,
                janela_s=_janela(regra, aqui, geral),
                severidade=_severidade(regra.get("severidade", "baixa"), f"{aqui}.severidade"),
            )
        )
    return tuple(faixas)


def _repeticoes(bruto: Any, caminho: str, geral: Geral) -> RegraRepeticoes:
    dados = _mapa(bruto, caminho)
    _sem_chaves_extras(
        dados,
        {
            "angulo",
            "sentido",
            "repouso",
            "alvo",
            "esperadas",
            "duracao_min_s",
            "severidade",
            "referencia",
        },
        caminho,
    )
    angulo = str(_get(dados, "angulo", caminho))
    if angulo not in JOINT_ANGLES:
        raise ErroDeConfiguracao(
            f"'{caminho}.angulo' = {angulo!r} não é um ângulo articular da US06"
        )
    sentido = str(_get(dados, "sentido", caminho))
    if sentido not in SENTIDOS:
        raise ErroDeConfiguracao(f"'{caminho}.sentido' precisa ser 'sobe' ou 'desce'")
    repouso = _numero(_get(dados, "repouso", caminho), f"{caminho}.repouso")
    alvo = None if dados.get("alvo") is None else _numero(dados["alvo"], f"{caminho}.alvo")
    referencia = (
        _referencia(dados["referencia"], f"{caminho}.referencia") if "referencia" in dados else None
    )
    if alvo is None and referencia is None:
        raise ErroDeConfiguracao(f"'{caminho}' precisa de 'alvo' e/ou 'referencia'")
    if alvo is not None and (
        (sentido == "sobe" and alvo <= repouso) or (sentido == "desce" and alvo >= repouso)
    ):
        raise ErroDeConfiguracao(
            f"'{caminho}': com sentido '{sentido}' o alvo ({alvo}) precisa estar além "
            f"do repouso ({repouso})"
        )
    esperadas = dados.get("esperadas")
    if esperadas is not None:
        if (
            not isinstance(esperadas, list)
            or len(esperadas) != 2
            or not all(isinstance(n, int) and n >= 0 for n in esperadas)
            or esperadas[0] > esperadas[1]
        ):
            raise ErroDeConfiguracao(f"'{caminho}.esperadas' precisa ser [mínimo, máximo] inteiros")
        esperadas = (esperadas[0], esperadas[1])
    return RegraRepeticoes(
        angulo=angulo,
        sentido=sentido,
        repouso=repouso,
        alvo=alvo,
        esperadas=esperadas,
        duracao_min_s=_numero(
            dados.get("duracao_min_s", geral.janela_minima_s),
            f"{caminho}.duracao_min_s",
            positivo=True,
        ),
        severidade=_severidade(dados.get("severidade", "baixa"), f"{caminho}.severidade"),
        referencia=referencia,
    )


def _referencia(bruto: Any, caminho: str) -> Referencia:
    dados = _mapa(bruto, caminho)
    _sem_chaves_extras(dados, {"repeticoes", "tolerancias", "fase_pico_graus"}, caminho)
    repeticoes = _get(dados, "repeticoes", caminho)
    if isinstance(repeticoes, bool) or not isinstance(repeticoes, int) or repeticoes < 1:
        raise ErroDeConfiguracao(f"'{caminho}.repeticoes' precisa ser um inteiro ≥ 1")
    tolerancias = _mapa(_get(dados, "tolerancias", caminho), f"{caminho}.tolerancias")
    if not tolerancias:
        raise ErroDeConfiguracao(f"'{caminho}.tolerancias' está vazio")
    for coluna in tolerancias:
        if coluna not in JOINT_ANGLES and coluna != "inclinacao_tronco":
            raise ErroDeConfiguracao(f"'{caminho}.tolerancias.{coluna}': coluna desconhecida")
    return Referencia(
        repeticoes=repeticoes,
        tolerancias={
            str(c): _numero(t, f"{caminho}.tolerancias.{c}", positivo=True)
            for c, t in tolerancias.items()
        },
        fase_pico_graus=_numero(
            _get(dados, "fase_pico_graus", caminho), f"{caminho}.fase_pico_graus", positivo=True
        ),
    )


def _salto(bruto: Any, caminho: str, chave_limiar: str, geral: Geral) -> RegraSalto:
    dados = _mapa(bruto, caminho)
    _sem_chaves_extras(dados, {chave_limiar, "tempo_max_s", "janela_s", "severidade"}, caminho)
    return RegraSalto(
        limiar=_numero(
            _get(dados, chave_limiar, caminho), f"{caminho}.{chave_limiar}", positivo=True
        ),
        tempo_max_s=_numero(
            _get(dados, "tempo_max_s", caminho), f"{caminho}.tempo_max_s", positivo=True
        ),
        janela_s=_janela(dados, caminho, geral),
        severidade=_severidade(dados.get("severidade", "media"), f"{caminho}.severidade"),
    )


def _areas(bruto: Any, caminho: str, geral: Geral) -> tuple[RegraArea, ...]:
    regras = []
    for area_id, regra in _mapa(bruto, caminho).items():
        aqui = f"{caminho}.{area_id}"
        regra = _mapa(regra, aqui)
        _sem_chaves_extras(regra, {"classes_proibidas", "janela_s", "severidade"}, aqui)
        classes = _get(regra, "classes_proibidas", aqui)
        if not isinstance(classes, list) or not classes:
            raise ErroDeConfiguracao(f"'{aqui}.classes_proibidas' precisa ser uma lista não vazia")
        regras.append(
            RegraArea(
                area_id=str(area_id),
                classes_proibidas=frozenset(str(c) for c in classes),
                janela_s=_janela(regra, aqui, geral),
                severidade=_severidade(regra.get("severidade", "media"), f"{aqui}.severidade"),
            )
        )
    return tuple(regras)


def _perfil(perfil_id: str, bruto: Any, geral: Geral) -> Perfil:
    caminho = f"perfis.{perfil_id}"
    dados = _mapa(bruto, caminho)
    _sem_chaves_extras(
        dados,
        {
            "tipo_video",
            "descricao",
            "faixas",
            "assimetrias",
            "repeticoes",
            "queda",
            "mudanca_brusca",
            "areas",
        },
        caminho,
    )
    tipo = str(_get(dados, "tipo_video", caminho))
    if tipo not in TIPOS_DE_VIDEO:
        raise ErroDeConfiguracao(
            f"'{caminho}.tipo_video' precisa ser um de {sorted(TIPOS_DE_VIDEO)}"
        )
    perfil = Perfil(
        id=perfil_id,
        tipo_video=tipo,
        descricao=str(dados.get("descricao", "")).strip(),
        faixas=_faixas(
            dados.get("faixas", {}),
            f"{caminho}.faixas",
            geral,
            set(JOINT_ANGLES) | {"inclinacao_tronco"},
        ),
        assimetrias=_faixas(
            dados.get("assimetrias", {}),
            f"{caminho}.assimetrias",
            geral,
            set(ASYMMETRIES),
            so_max=True,
        ),
        repeticoes=(
            _repeticoes(dados["repeticoes"], f"{caminho}.repeticoes", geral)
            if "repeticoes" in dados
            else None
        ),
        queda=(
            _salto(dados["queda"], f"{caminho}.queda", "descida_min_torsos", geral)
            if "queda" in dados
            else None
        ),
        mudanca_brusca=(
            _salto(
                dados["mudanca_brusca"], f"{caminho}.mudanca_brusca", "variacao_min_graus", geral
            )
            if "mudanca_brusca" in dados
            else None
        ),
        areas=_areas(dados.get("areas", {}), f"{caminho}.areas", geral),
    )
    if not perfil.usa_pose and not perfil.areas:
        raise ErroDeConfiguracao(f"'{caminho}' não tem nenhuma regra")
    return perfil


def _geral(bruto: Any) -> Geral:
    dados = _mapa(bruto, "geral")
    score = _mapa(_get(dados, "score", "geral"), "geral.score")
    fracao = _numero(
        _get(dados, "fracao_permanencia", "geral"), "geral.fracao_permanencia", positivo=True
    )
    if fracao > 1:
        raise ErroDeConfiguracao("'geral.fracao_permanencia' precisa estar em (0, 1]")
    return Geral(
        janela_minima_s=_numero(
            _get(dados, "janela_minima_s", "geral"), "geral.janela_minima_s", positivo=True
        ),
        lacuna_max_s=_numero(_get(dados, "lacuna_max_s", "geral"), "geral.lacuna_max_s"),
        fracao_permanencia=fracao,
        score=Score(
            saturacao_graus=_numero(
                _get(score, "saturacao_graus", "geral.score"),
                "geral.score.saturacao_graus",
                positivo=True,
            ),
            saturacao_torsos=_numero(
                _get(score, "saturacao_torsos", "geral.score"),
                "geral.score.saturacao_torsos",
                positivo=True,
            ),
        ),
        cobertura_minima_pct=_numero(
            _get(dados, "cobertura_minima_pct", "geral"), "geral.cobertura_minima_pct"
        ),
    )


def config_de_dict(dados: Mapping[str, Any], fonte: Path | None = None) -> ConfigDesvios:
    dados = _mapa(dados, "raiz")
    versao = str(_get(dados, "versao", "raiz"))
    geral = _geral(_get(dados, "geral", "raiz"))
    perfis = _mapa(_get(dados, "perfis", "raiz"), "perfis")
    if not perfis:
        raise ErroDeConfiguracao("'perfis' está vazio")
    return ConfigDesvios(
        versao=versao,
        geral=geral,
        perfis={pid: _perfil(str(pid), bruto, geral) for pid, bruto in perfis.items()},
        fonte=fonte,
    )


def carregar_config(caminho: str | Path = CAMINHO_PADRAO) -> ConfigDesvios:
    caminho = Path(caminho)
    if not caminho.is_file():
        raise ErroDeConfiguracao(f"configuração da US08 não encontrada: {caminho}")
    with caminho.open(encoding="utf-8") as fh:
        dados = yaml.safe_load(fh)
    if not isinstance(dados, Mapping):
        raise ErroDeConfiguracao(f"{caminho}: o arquivo deve conter um mapeamento YAML")
    return config_de_dict(dados, fonte=caminho)


__all__ = [
    "CAMINHO_PADRAO",
    "ConfigDesvios",
    "ErroDeConfiguracao",
    "Faixa",
    "Geral",
    "Perfil",
    "Referencia",
    "RegraArea",
    "RegraRepeticoes",
    "RegraSalto",
    "Score",
    "carregar_config",
    "config_de_dict",
]
