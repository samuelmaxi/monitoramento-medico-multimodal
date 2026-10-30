"""Motor de regras: função pura que decide prioridade, ação e canais de um achado.

Políticas e regras ficam em ``config/alertas_regras.yaml``. Sem regra específica
para ``(modalidade, tipo)``, vale a severidade do próprio achado. Por isso qualquer
``event_type`` do catálogo gera alerta sem mudar código, inclusive de modalidades
que ainda não têm detector.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml

from contratos import SEM_ACHADOS, TIPOS_DE_EVENTO, EventoAchado, Modalidade, Severidade

PRIORIDADES_ALERTAVEIS = (Severidade.BAIXA, Severidade.MEDIA, Severidade.ALTA)
CURINGA = "*"
CAMINHO_REGRAS = Path("config/alertas_regras.yaml")


@dataclass(frozen=True)
class Politica:
    """Tratamento de um alerta de uma prioridade: canais, deduplicação e escalonamento."""

    prioridade: Severidade
    canais: tuple[str, ...]
    janela_dedup: timedelta
    escalar_apos_ocorrencias: int
    escalar_apos: timedelta
    acao_padrao: str


@dataclass(frozen=True)
class RegraEspecifica:
    modalidade: str
    tipo: str  # event_type do catálogo ou CURINGA ("*") para a modalidade inteira
    prioridade: Severidade
    acao_sugerida: str | None


@dataclass(frozen=True)
class Decisao:
    prioridade: Severidade
    acao_sugerida: str
    canais: tuple[str, ...]
    janela_dedup: timedelta
    origem: str


class Regras:
    def __init__(
        self, politicas: Mapping[Severidade, Politica], especificas: list[RegraEspecifica]
    ):
        ausentes = [p.value for p in PRIORIDADES_ALERTAVEIS if p not in politicas]
        if ausentes:
            raise ValueError(f"políticas ausentes para as prioridades: {', '.join(ausentes)}")
        self._politicas = dict(politicas)
        self._exatas = {(r.modalidade, r.tipo): r for r in especificas if r.tipo != CURINGA}
        self._por_modalidade = {r.modalidade: r for r in especificas if r.tipo == CURINGA}

    def politica(self, prioridade: Severidade) -> Politica:
        return self._politicas[prioridade]

    def regra_para(self, modalidade: str, tipo: str) -> RegraEspecifica | None:
        return self._exatas.get((modalidade, tipo)) or self._por_modalidade.get(modalidade)


def carregar_regras(caminho: str | Path = CAMINHO_REGRAS) -> Regras:
    with Path(caminho).open(encoding="utf-8") as arquivo:
        return regras_de_dict(yaml.safe_load(arquivo) or {})


def regras_de_dict(dados: Mapping[str, Any]) -> Regras:
    politicas = {}
    for nome, bruto in (dados.get("politicas") or {}).items():
        prioridade = _prioridade(nome)
        politicas[prioridade] = Politica(
            prioridade=prioridade,
            canais=tuple(str(c) for c in bruto.get("canais") or []),
            janela_dedup=timedelta(minutes=float(bruto["janela_dedup_min"])),
            escalar_apos_ocorrencias=int(bruto["escalar_apos_ocorrencias"]),
            escalar_apos=timedelta(minutes=float(bruto["escalar_apos_min"])),
            acao_padrao=str(bruto["acao_padrao"]),
        )

    especificas: list[RegraEspecifica] = []
    vistas: set[tuple[str, str]] = set()
    for item in dados.get("regras") or []:
        modalidade, tipo = str(item["modalidade"]), str(item["tipo"])
        _validar_tipo(modalidade, tipo)
        if (modalidade, tipo) in vistas:
            raise ValueError(f"regra duplicada para {modalidade}/{tipo}")
        vistas.add((modalidade, tipo))
        especificas.append(
            RegraEspecifica(
                modalidade=modalidade,
                tipo=tipo,
                prioridade=_prioridade(item["prioridade"]),
                acao_sugerida=item.get("acao_sugerida"),
            )
        )
    return Regras(politicas, especificas)


def avaliar(evento: EventoAchado, regras: Regras) -> Decisao | None:
    """Decide se o achado vira alerta. ``info`` e ``sem_achados`` nunca viram alerta."""
    if evento.severity not in PRIORIDADES_ALERTAVEIS or evento.event_type == SEM_ACHADOS:
        return None

    regra = regras.regra_para(evento.modality.value, evento.event_type)
    if regra is None:
        prioridade, origem, acao = evento.severity, f"padrao:{evento.severity.value}", None
    else:
        prioridade = regra.prioridade
        origem = f"regra:{regra.modalidade}/{regra.tipo}"
        acao = regra.acao_sugerida

    politica = regras.politica(prioridade)
    return Decisao(
        prioridade=prioridade,
        acao_sugerida=acao or politica.acao_padrao,
        canais=politica.canais,
        janela_dedup=politica.janela_dedup,
        origem=origem,
    )


def _prioridade(valor: Any) -> Severidade:
    try:
        prioridade = Severidade(valor)
    except ValueError as exc:
        raise ValueError(f"prioridade desconhecida: {valor!r}") from exc
    if prioridade not in PRIORIDADES_ALERTAVEIS:
        raise ValueError(f"prioridade {valor!r} não gera alerta; use alta, media ou baixa")
    return prioridade


def _validar_tipo(modalidade: str, tipo: str) -> None:
    try:
        mod = Modalidade(modalidade)
    except ValueError as exc:
        raise ValueError(f"modalidade desconhecida na regra: {modalidade!r}") from exc
    if tipo == CURINGA:
        return
    if tipo == SEM_ACHADOS or tipo not in TIPOS_DE_EVENTO[mod]:
        raise ValueError(
            f"tipo {tipo!r} não existe na modalidade {modalidade!r} (contratos/catalogo.py)"
        )
