"""Transições de entrada/saída em área crítica — US07.

O monitor é a peça que impede o evento por frame. Ele guarda, por
``(área, objeto)``, se o objeto estava dentro ou fora no quadro anterior, e só
emite evento quando o estado muda:

===================== ============ =========
Quadro anterior      Quadro atual Evento
===================== ============ =========
fora                 dentro       ``entrada_area_critica``
dentro               dentro       nenhum
dentro               fora         ``saida_area_critica``
fora                 fora         nenhum
===================== ============ =========

Identidade do objeto
--------------------
Com rastreamento ligado (``DetectorYolo`` com ByteTrack) a chave é o
``track_id``, então duas pessoas na mesma área não se confundem. Sem
rastreamento, cai para ``(área, classe)``: nesse modo duas pessoas da mesma
classe na mesma área compartilham estado. O modo sem rastreamento existe para os
testes e para vídeos sem movimento, e está documentado como limitação em
``docs/arquitetura/us07_deteccao_areas_criticas.md``.

Robustez
--------
Duas proteções evitam eventos falsos de sistemas de vigilância:

- ``persistencia_quadros``: uma detecção que some por N quadros (oclusão,
  detecção perdida) não gera ``saida``; a ausência só encerra o estado depois.
- ``primeira_observacao_entra``: a primeira vez que um objeto é visto já dentro
  da área emite ``entrada``. Sem isso, o primeiro quadro do vídeo geraria um
  evento falso sempre que alguém já estivesse na área crítica. Desligue com
  ``False`` quando o início do vídeo for cropped e a entrada importar.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from .areas import AreaCritica, AvaliacaoContencao, ConjuntoAreas
from .modelos import Deteccao

log = logging.getLogger(__name__)

PERSISTENCIA_PADRAO = 2
"""Quadros sem detecção tolerados antes de encerrar o estado interno."""


class Transicao(StrEnum):
    """Direção da mudança de estado; o valor é o sufixo do ``event_type``."""

    ENTRADA = "entrada_area_critica"
    SAIDA = "saida_area_critica"


@dataclass(frozen=True, slots=True)
class EventoAreaCritica:
    """Mudança de estado observada em um quadro, ainda sem o contrato da US04."""

    transicao: Transicao
    deteccao: Deteccao
    id_area: str
    nome_area: str
    contencao: AvaliacaoContencao
    track_id: int | None = None
    dentro_ha_quadros: int = 0
    """Quantos quadros seguidos o objeto estava dentro da área (1 na entrada)."""

    @property
    def event_type(self) -> str:
        return self.transicao.value

    @property
    def chave(self) -> tuple[str, str | int]:
        return (self.id_area, self.track_id or self.deteccao.class_name)


@dataclass(slots=True)
class _Estado:
    """Estado interno de um objeto dentro de uma área."""

    dentro: bool
    contencao: AvaliacaoContencao
    deteccao: Deteccao
    area: str
    track_id: int | None = None
    ausente_ha: int = 0
    quadros_dentro: int = 0


@dataclass(slots=True)
class MonitorAreas:
    """Máquina de estado de entrada/saída por área crítica.

    Não conhece OpenCV, nem Ultralytics, nem o contrato de eventos: recebe
    detecções já filtradas e devolve :class:`EventoAreaCritica`.
    """

    areas: ConjuntoAreas
    criterio: str = "fracao"
    persistencia_quadros: int = PERSISTENCIA_PADRAO
    primeira_observacao_entra: bool = False
    _estados: dict[tuple[str, str | int], _Estado] = field(default_factory=dict)
    _vistos_no_quadro: set[tuple[str, str | int]] = field(default_factory=set)

    def processar(
        self, deteccoes: Iterable[Deteccao]
    ) -> list[EventoAreaCritica]:
        """Processa um quadro e devolve as transições detectadas.

        Args:
            deteccoes: detecções do quadro atual (já filtradas por classe e
                confiança). Deduplicação de ``track_id`` não é feita aqui: quem
                chama deve entregar uma detecção por objeto.

        Returns:
            Transições na ordem em que foram detectadas: entradas e saídas deste
            quadro. Quadros sem mudança devolvem lista vazia.
        """
        eventos: list[EventoAreaCritica] = []
        self._vistos_no_quadro = set()

        for deteccao in deteccoes:
            for area in self.areas:
                id_area = area.id
                contencao = area.contem(deteccao.caixa, criterio=self.criterio)
                if not area.aceita_classe(deteccao.class_name):
                    continue
                chave = (id_area, self._identidade(deteccao))
                self._vistos_no_quadro.add(chave)
                eventos.extend(self._atualizar(chave, area, deteccao, contencao))

        eventos.extend(self._encerrar_ausentes())
        return eventos

    def estado_atual(self, id_area: str, identidade: str | int) -> bool | None:
        """``True``/``False`` se o objeto está dentro/fora, ``None`` se desconhecido."""
        estado = self._estados.get((id_area, identidade))
        return None if estado is None else estado.dentro

    def reiniciar(self) -> None:
        """Limpa o estado. Use ao trocar de vídeo/fonte."""
        self._estados.clear()
        self._vistos_no_quadro.clear()

    @property
    def objetos_monitorados(self) -> int:
        return len(self._estados)

    @staticmethod
    def _identidade(deteccao: Deteccao) -> str | int:
        return deteccao.track_id if deteccao.track_id is not None else deteccao.class_name

    def _atualizar(
        self,
        chave: tuple[str, str | int],
        area: AreaCritica,
        deteccao: Deteccao,
        contencao: AvaliacaoContencao,
    ) -> list[EventoAreaCritica]:
        estado = self._estados.get(chave)

        if estado is None:
            self._estados[chave] = _Estado(
                dentro=contencao.dentro,
                contencao=contencao,
                deteccao=deteccao,
                area=chave[0],
                track_id=deteccao.track_id,
                ausente_ha=0,
                quadros_dentro=1 if contencao.dentro else 0,
            )
            if contencao.dentro and self.primeira_observacao_entra:
                return [self._evento(Transicao.ENTRADA, area, deteccao, contencao, 1)]
            return []

        mudou = estado.dentro != contencao.dentro
        estado.dentro = contencao.dentro
        estado.contencao = contencao
        estado.deteccao = deteccao
        estado.track_id = deteccao.track_id
        estado.ausente_ha = 0
        estado.quadros_dentro = estado.quadros_dentro + 1 if contencao.dentro else 0

        if not mudou:
            return []
        transicao = Transicao.ENTRADA if contencao.dentro else Transicao.SAIDA
        return [self._evento(transicao, area, deteccao, contencao, estado.quadros_dentro)]

    def _encerrar_ausentes(self) -> list[EventoAreaCritica]:
        """Fecha estados cujos objetos sumiram do quadro além da persistência."""
        eventos: list[EventoAreaCritica] = []
        for chave in [c for c in self._estados if c not in self._vistos_no_quadro]:
            estado = self._estados[chave]
            estado.ausente_ha += 1
            if estado.ausente_ha <= self.persistencia_quadros:
                continue
            if estado.dentro:
                area = self.areas.por_id(estado.area)
                nome_area = area.nome if area else estado.area
                eventos.append(
                    EventoAreaCritica(
                        transicao=Transicao.SAIDA,
                        deteccao=estado.deteccao,
                        id_area=estado.area,
                        nome_area=nome_area,
                        contencao=AvaliacaoContencao(
                            dentro=False, fracao=0.0, criterio=self.criterio
                        ),
                        track_id=estado.track_id,
                        dentro_ha_quadros=estado.quadros_dentro,
                    )
                )
                log.debug(
                    "objeto %s saiu da área %s por ausência em %d quadros",
                    chave,
                    estado.area,
                    estado.ausente_ha,
                )
            del self._estados[chave]
        return eventos

    @staticmethod
    def _evento(
        transicao: Transicao,
        area: AreaCritica,
        deteccao: Deteccao,
        contencao: AvaliacaoContencao,
        quadros_dentro: int,
    ) -> EventoAreaCritica:
        return EventoAreaCritica(
            transicao=transicao,
            deteccao=deteccao,
            id_area=area.id,
            nome_area=area.nome,
            contencao=contencao,
            track_id=deteccao.track_id,
            dentro_ha_quadros=quadros_dentro,
        )


def transicoes_de(
    monitor: MonitorAreas, sequencia: Sequence[Iterable[Deteccao]]
) -> list[EventoAreaCritica]:
    """Atalho de teste/exemplo: roda o monitor sobre vários quadros."""
    eventos: list[EventoAreaCritica] = []
    for deteccoes in sequencia:
        eventos.extend(monitor.processar(deteccoes))
    return eventos


__all__ = [
    "PERSISTENCIA_PADRAO",
    "EventoAreaCritica",
    "MonitorAreas",
    "Transicao",
    "transicoes_de",
]
