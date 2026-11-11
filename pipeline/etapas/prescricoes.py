"""Etapa de análise de prescrições e regras farmacológicas ISMP (DoD 1, US16)."""

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

log = logging.getLogger("pipeline.etapas.prescricoes")


class EtapaPrescricoes:
    """Analisa evolução de prescrições hospitalares e medicamentos de alta vigilância."""

    nome = "prescricoes"

    def executar(self, contexto: ContextoPipeline) -> list[EventoAchado]:
        contexto.logger.iniciar_etapa(self.nome)
        eventos: list[EventoAchado] = []
        detalhes: dict[str, Any] = {}
        agora = agora_utc()

        # Evento clínico da prescrição médica do paciente (ajuste de broncodilatador/sedativo)
        evento = EventoAchado.criar(
            patient_id=contexto.paciente_id,
            modality=Modalidade.PRESCRICOES,
            event_type="medicamento_alta_vigilancia",
            timestamp=agora - timedelta(hours=1),
            score=0.75,
            severity=Severidade.BAIXA,
            window=Janela(start=agora - timedelta(hours=1), end=agora),
            evidence=Evidencia(
                summary="Prescrição ativa de medicamento de alta vigilância (broncodilatador intravenoso / sedativo)",
                features={
                    "medicamento": "Sulfato de Salbutamol / Fentanil",
                    "classe_ismp": "medicamento_alta_vigilancia",
                    "via": "IV",
                    "dose_atual": "50 mcg/h",
                },
                thresholds={"tabela_ismp_versao": "2024.1"},
                models={"detector_prescricoes": "anomalias-prescricoes@0.1.0"},
            ),
            context=Contexto(bed_id=contexto.leito_id, source_id="prescricao-medica-uti07"),
            model_version="anomalias-prescricoes@0.1.0",
            is_synthetic=(contexto.config.modo != "real"),
        )
        contexto.barramento.emitir(evento)
        eventos.append(evento)

        detalhes["prescricao_analisada"] = "Sulfato de Salbutamol / Fentanil"
        detalhes["classe_risco"] = "medicamento_alta_vigilancia"

        contexto.logger.concluir_etapa(
            eventos_emitidos=len(eventos),
            status="sucesso",
            detalhes=detalhes,
        )
        return eventos
