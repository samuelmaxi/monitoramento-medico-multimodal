"""Etapa de sinais vitais com simulação de streaming e detecção NEWS2 (DoD 1, DoD 3, US15)."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from contratos import EventoAchado, agora_utc
from pipeline.etapas.base import ContextoPipeline
from sinais import DetectorSinais, Medicao

log = logging.getLogger("pipeline.etapas.sinais")


def gerar_serie_streaming_clinica(paciente: str, inicio: datetime) -> list[Medicao]:
    """Série cronológica com transição de estabilidade para deterioração respiratória."""
    t = inicio
    medicoes: list[Medicao] = [
        # T+0 min: Paciente estável na admissão
        Medicao(paciente, t + timedelta(minutes=0), "fc", 76.0),
        Medicao(paciente, t + timedelta(minutes=0, seconds=5), "pas", 122.0),
        Medicao(paciente, t + timedelta(minutes=0, seconds=10), "pad", 78.0),
        Medicao(paciente, t + timedelta(minutes=0, seconds=15), "fr", 16.0),
        Medicao(paciente, t + timedelta(minutes=0, seconds=20), "spo2", 97.0),
        Medicao(paciente, t + timedelta(minutes=0, seconds=25), "temp", 36.6),
        Medicao(paciente, t + timedelta(minutes=0, seconds=30), "alerta", 1.0),
        Medicao(paciente, t + timedelta(minutes=0, seconds=35), "o2_suplementar", 0.0),

        # T+30 min: Início de esforço respiratório leve
        Medicao(paciente, t + timedelta(minutes=30), "fc", 88.0),
        Medicao(paciente, t + timedelta(minutes=30, seconds=10), "fr", 21.0),
        Medicao(paciente, t + timedelta(minutes=30, seconds=20), "spo2", 93.0),
        Medicao(paciente, t + timedelta(minutes=30, seconds=30), "pas", 130.0),

        # T+60 min: NEWS2 resposta urgente (SpO2 91%, FR 24, FC 102)
        Medicao(paciente, t + timedelta(minutes=60), "fc", 104.0),
        Medicao(paciente, t + timedelta(minutes=60, seconds=10), "fr", 24.0),
        Medicao(paciente, t + timedelta(minutes=60, seconds=20), "spo2", 91.0),
        Medicao(paciente, t + timedelta(minutes=60, seconds=30), "pas", 138.0),

        # T+90 min: NEWS2 emergência clínica (SpO2 88%, FR 27, FC 124, O2 suplementar)
        Medicao(paciente, t + timedelta(minutes=90), "fc", 124.0),
        Medicao(paciente, t + timedelta(minutes=90, seconds=10), "fr", 27.0),
        Medicao(paciente, t + timedelta(minutes=90, seconds=20), "spo2", 88.0),
        Medicao(paciente, t + timedelta(minutes=90, seconds=30), "o2_suplementar", 1.0),
        Medicao(paciente, t + timedelta(minutes=90, seconds=40), "pas", 146.0),
    ]
    return medicoes


class EtapaSinaisVitais:
    """Simula streaming de sinais vitais e processa via DetectorSinais em tempo real."""

    nome = "sinais_vitais"

    def executar(self, contexto: ContextoPipeline) -> list[EventoAchado]:
        contexto.logger.iniciar_etapa(self.nome)
        eventos: list[EventoAchado] = []
        detalhes: dict[str, Any] = {}

        detector = DetectorSinais()
        agora = agora_utc()
        inicio_sim = agora - timedelta(minutes=95)

        # Gera série de streaming
        medicoes = gerar_serie_streaming_clinica(contexto.paciente_id, inicio_sim)
        detalhes["total_medicoes_streaming"] = len(medicoes)

        velocidade = contexto.config.fator_velocidade
        anterior_instante: datetime | None = None

        for idx, medicao in enumerate(medicoes, start=1):
            # Temporização proporcional para demonstração em tempo real
            if velocidade > 0 and anterior_instante is not None:
                delta_s = (medicao.instante - anterior_instante).total_seconds()
                atraso = min(delta_s / velocidade, 0.4)  # Limita a 400ms para manter a demo dinâmica
                if atraso > 0.01:
                    time.sleep(atraso)

            anterior_instante = medicao.instante

            # Processamento streaming: uma medição por vez
            novos = detector.processar(medicao)
            for ev in novos:
                # Garante vínculo com o leito no contexto do evento
                ev_com_leito = ev.model_copy(
                    update={"context": ev.context.model_copy(update={"bed_id": contexto.leito_id})}
                    if ev.context
                    else None
                )
                contexto.barramento.emitir(ev_com_leito)
                eventos.append(ev_com_leito)

        tipos_gerados = [e.event_type for e in eventos]
        detalhes["eventos_detectados"] = tipos_gerados
        detalhes["artefatos_descartados"] = dict(detector.artefatos)
        detalhes["modo_streaming"] = f"velocidade={velocidade}x"

        contexto.logger.concluir_etapa(
            eventos_emitidos=len(eventos),
            status="sucesso",
            detalhes=detalhes,
        )
        return eventos

