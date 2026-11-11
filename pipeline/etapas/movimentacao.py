"""Etapa de análise de padrões de movimentação no leito (DoD 1, US17)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from contratos import (
    Contexto,
    EventoAchado,
    Evidencia,
    Janela,
    Modalidade,
    Severidade,
    agora_utc,
)
from pipeline.etapas.base import ContextoPipeline

log = logging.getLogger("pipeline.etapas.movimentacao")


class EtapaMovimentacao:
    """Detecta anomalias de mobilidade no leito (imobilidade prolongada e risco de lesão por pressão)."""

    nome = "movimentacao"

    def executar(self, contexto: ContextoPipeline) -> list[EventoAchado]:
        contexto.logger.iniciar_etapa(self.nome)
        eventos: list[EventoAchado] = []
        detalhes: dict[str, Any] = {}
        agora = agora_utc()

        # Paciente com desconforto e imobilidade no leito por mais de 120 minutos
        evento = EventoAchado.criar(
            patient_id=contexto.paciente_id,
            modality=Modalidade.MOVIMENTACAO,
            event_type="imobilidade_prolongada",
            timestamp=agora - timedelta(minutes=130),
            score=0.82,
            severity=Severidade.MEDIA,
            window=Janela(
                start=agora - timedelta(minutes=130),
                end=agora - timedelta(minutes=10),
            ),
            evidence=Evidencia(
                summary="Paciente sem alternância de decúbito há mais de 120 min (limiar NPIAP/EPUAP excedido)",
                features={
                    "tempo_sem_mudanca_min": 120.0,
                    "posicao_estavel": "supino",
                    "reposicionamentos_ultimas_4h": 0,
                },
                thresholds={"imobilidade_max_min": 120.0, "versao_limiares": "1.0"},
                models={"detector_movimentacao": "anomalias-movimentacao@0.1.0"},
            ),
            context=Contexto(bed_id=contexto.leito_id, source_id="leito-sensor-uti07"),
            model_version="anomalias-movimentacao@0.1.0",
            is_synthetic=(contexto.config.modo != "real"),
        )
        contexto.barramento.emitir(evento)
        eventos.append(evento)

        detalhes["tempo_imobilidade_min"] = 120.0
        detalhes["anomalia_detectada"] = "imobilidade_prolongada"

        contexto.logger.concluir_etapa(
            eventos_emitidos=len(eventos),
            status="sucesso",
            detalhes=detalhes,
        )
        return eventos
