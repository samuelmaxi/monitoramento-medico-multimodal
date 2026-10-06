"""Tradução da transição de estado para o contrato de eventos da US04.

Este é o único lugar da US07 que conhece :mod:`contratos`. Ele recebe
:class:`~video.monitor.EventoAreaCritica` e devolve um
:class:`contratos.EventoAchado`, ou seja:

- nada é inventado no schema: os tipos ``entrada_area_critica`` e
  ``saida_area_critica`` já estão no catálogo da modalidade ``video``;
- o emissor usado é o da US04 (:class:`contratos.Emissor`), então a US07 pluga
  no ``BarramentoLocal`` sem mudar nada;
- ``patient_id`` é o pseudônimo ``pt_<24 hex>`` que a configuração exige.

Mapeamento campo a campo
------------------------
=========================  ==========================================
US07                       ``EventoAchado`` (v1.0.0)
=========================  ==========================================
direção da transição       ``event_type`` (``entrada``/``saida``)
instante do quadro          ``timestamp`` (+ ``window`` do intervalo anterior)
confiança da detecção       ``score``
regra do módulo             ``severity`` (declarada na configuração)
classe + ROI + fração       ``evidence.features``
limiares aplicados          ``evidence.thresholds``
pesos do YOLO              ``evidence.models``
câmera/leito                ``context``
=========================  ==========================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from contratos import (
    Artefato,
    Contexto,
    Emissor,
    EventoAchado,
    Evidencia,
    Modalidade,
    Severidade,
    agora_utc,
)

from .config import VERSAO_MODULO, Configuracao
from .modelos import MetadadosVideo
from .monitor import EventoAreaCritica, Transicao

log = logging.getLogger(__name__)

RESUMO_ENTRADA = "{classe} entrou na área crítica '{area}' (confiança {confianca:.2f})"
RESUMO_SAIDA = "{classe} saiu da área crítica '{area}' (confiança {confianca:.2f})"


@dataclass(frozen=True, slots=True)
class ContextoTraducao:
    """Tudo que a tradução precisa saber além da própria transição."""

    model_version: str = VERSAO_MODULO
    descricao_modelo: str = "yolov8n@indisponivel"
    severidade: str = "baixa"
    contexto: Contexto | None = None
    thresholds: dict[str, Any] | None = None
    metadados: MetadadosVideo | None = None
    artefato: Artefato | None = None


class TradutorEventos:
    """Constrói e emite :class:`~contratos.EventoAchado` da US04.

    A classe não conhece o detector nem o OpenCV: recebe a configuração já
    lida. Isso mantém o contrato testável com um ``DetectorFalso``.
    """

    def __init__(self, config: Configuracao) -> None:
        self.config = config

    # ------------------------------------------------------------------ #
    # Timestamp
    # ------------------------------------------------------------------ #

    def instante_do_quadro(
        self, tempo_s: float, *, detectado_em: datetime | None = None
    ) -> datetime:
        """Converte o tempo relativo do vídeo no ``timestamp`` UTC do evento.

        Se a fonte declarar ``inicio_utc`` (instante de gravação vindo do sistema
        externo), o evento recebe esse instante deslocado pelo tempo do vídeo.
        Sem ``inicio_utc``, soma o tempo relativo do vídeo ao início da análise,
        mantendo os eventos de diferentes frames temporalmente ordenados.

        Args:
            tempo_s: tempo em segundos desde o início do arquivo/stream.
            detectado_em: instante de referência; por padrão, ``agora_utc()``.
        """
        inicio = self.config.fonte.inicio_utc
        if inicio is None:
            base = detectado_em or agora_utc()
        else:
            try:
                base = datetime.fromisoformat(inicio.replace("Z", "+00:00"))
            except ValueError as erro:
                raise ValueError(
                    f"fonte.inicio_utc inválido: {inicio!r} (use ISO-8601 UTC)"
                ) from erro
        if base.tzinfo is None or base.utcoffset() != timedelta(0):
            raise ValueError("fonte.inicio_utc/detectado_em precisa estar em UTC")
        return base + timedelta(seconds=tempo_s)

    # ------------------------------------------------------------------ #
    # Construção
    # ------------------------------------------------------------------ #

    def montar(
        self,
        transicao_area: EventoAreaCritica,
        *,
        contexto: ContextoTraducao,
        detectado_em: datetime | None = None,
    ) -> EventoAchado:
        """Monta o evento da US04 a partir da transição, sem emitir."""
        deteccao = transicao_area.deteccao
        instante = self.instante_do_quadro(deteccao.tempo_s, detectado_em=detectado_em)

        features: dict[str, Any] = {
            "classe": deteccao.class_name,
            "classe_id": deteccao.class_id,
            "confianca_deteccao": round(deteccao.confianca, 4),
            "area_critica_id": transicao_area.id_area,
            "area_critica_nome": transicao_area.nome_area,
            "transicao": transicao_area.transicao.name.lower(),
            "fracao_na_area": round(transicao_area.contencao.fracao, 4),
            "criterio_roi": transicao_area.contencao.criterio,
            "frame_index": deteccao.frame_index,
            "tempo_video_s": round(deteccao.tempo_s, 3),
            "x1": round(deteccao.caixa.x1, 1),
            "y1": round(deteccao.caixa.y1, 1),
            "x2": round(deteccao.caixa.x2, 1),
            "y2": round(deteccao.caixa.y2, 1),
            "quadros_dentro": transicao_area.dentro_ha_quadros,
        }
        if deteccao.track_id is not None:
            features["track_id"] = deteccao.track_id
        if contexto.metadados is not None:
            features["fps"] = contexto.metadados.fps
            features["resolucao"] = (
                f"{contexto.metadados.largura}x{contexto.metadados.altura}"
            )

        resumo = (
            RESUMO_ENTRADA if transicao_area.transicao is Transicao.ENTRADA else RESUMO_SAIDA
        ).format(
            classe=deteccao.class_name,
            area=transicao_area.nome_area,
            confianca=deteccao.confianca,
        )

        evidencia = Evidencia(
            summary=resumo,
            features=features,
            thresholds=contexto.thresholds or {},
            models={"yolov8": contexto.descricao_modelo},
            artifacts=[contexto.artefato] if contexto.artefato else [],
        )

        return EventoAchado.criar(
            patient_id=self.config.fonte.patient_id,
            modality=Modalidade.VIDEO,
            event_type=transicao_area.event_type,
            timestamp=instante,
            score=deteccao.confianca,
            severity=Severidade(contexto.severidade),
            evidence=evidencia,
            model_version=contexto.model_version,
            context=contexto.contexto,
        )

    def contexto_de(
        self,
        *,
        descricao_modelo: str,
        metadados: MetadadosVideo | None = None,
        artefato: Artefato | None = None,
    ) -> ContextoTraducao:
        """Monta o :class:`ContextoTraducao` com os limiares e o ``context``."""
        thresholds: dict[str, Any] = {
            "confianca_minima": self.config.detector.confianca,
            "iou_nms": self.config.detector.iou,
            "criterio_roi": self.config.monitor.criterio,
            "persistencia_quadros": self.config.monitor.persistencia_quadros,
        }
        for area in self.config.areas:
            thresholds[f"area_minima_{area.id}"] = area.area_minima

        contexto = Contexto(
            source_id=self.config.fonte.id,
            bed_id=self.config.fonte.bed_id,
            encounter_id=self.config.fonte.encounter_id,
            procedure_type=self.config.fonte.procedure_type,
        )
        return ContextoTraducao(
            model_version=VERSAO_MODULO,
            descricao_modelo=descricao_modelo,
            severidade=self.config.evento.severidade,
            contexto=contexto,
            thresholds=thresholds,
            metadados=metadados,
            artefato=artefato,
        )

    def emitir(
        self,
        transicao_area: EventoAreaCritica,
        emissor: Emissor,
        *,
        contexto: ContextoTraducao,
        detectado_em: datetime | None = None,
    ) -> EventoAchado:
        """Monta e publica o evento no barramento da US04.

        Returns:
            O evento emitido, para inspeção em teste e para o log.
        """
        evento = self.montar(transicao_area, contexto=contexto, detectado_em=detectado_em)
        emissor.emitir(evento)
        log.debug(
            "evento %s emitido para %s/%s em t=%.2fs",
            evento.event_type,
            transicao_area.id_area,
            transicao_area.deteccao.class_name,
            transicao_area.deteccao.tempo_s,
        )
        return evento


__all__ = ["ContextoTraducao", "TradutorEventos"]
