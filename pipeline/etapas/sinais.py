"""Etapa de sinais vitais com simulação de streaming e detecção NEWS2 (DoD 1, DoD 3, US15)."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from contratos import EventoAchado, agora_utc
from pipeline.etapas.base import ContextoPipeline
from sinais import DetectorSinais, Medicao, mimic

log = logging.getLogger("pipeline.etapas.sinais")

LIMITE_ESPERA_S = 0.05


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


def carregar_replay_mimic(
    caminho: Path, subject_id: int, janela_horas: float, paciente: str, fim: datetime
) -> list[Medicao]:
    """Trecho final do registro de um paciente do MIMIC-IV Demo, deslocado para terminar em ``fim``.

    As datas do MIMIC são deslocadas por paciente; só os intervalos entre as medições valem.
    """
    df = mimic.carregar(caminho)
    df = df[df.subject_id == subject_id]
    if df.empty:
        raise ValueError(f"subject_id {subject_id} não existe em {caminho}")
    ultimo = df.instante.max()
    df = df[df.instante >= ultimo - timedelta(hours=janela_horas)]
    return [
        Medicao(
            paciente, fim - (ultimo - linha.instante).to_pytimedelta(), linha.sinal, linha.valor
        )
        for linha in df.itertuples()
    ]


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

        medicoes = self._medicoes(contexto, agora, inicio_sim, detalhes)
        detalhes["total_medicoes_streaming"] = len(medicoes)

        velocidade = contexto.config.fator_velocidade
        anterior_instante: datetime | None = None

        for idx, medicao in enumerate(medicoes, start=1):
            # Temporização proporcional para demonstração em tempo real
            if velocidade > 0 and anterior_instante is not None:
                delta_s = (medicao.instante - anterior_instante).total_seconds()
                atraso = min(delta_s / velocidade, LIMITE_ESPERA_S)  # mantém a demo dinâmica
                if atraso > 0.005:
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

    def _medicoes(
        self,
        contexto: ContextoPipeline,
        agora: datetime,
        inicio_sim: datetime,
        detalhes: dict[str, Any],
    ) -> list[Medicao]:
        cfg = contexto.config
        fonte = cfg.sinais_fonte
        if fonte == "auto":
            fonte = "mimic" if cfg.mimic_path.is_file() else "sintetico"
        if fonte == "mimic":
            if not cfg.mimic_path.is_file():
                raise FileNotFoundError(
                    f"{cfg.mimic_path} não existe; rode scripts/baixar_sinais.py"
                )
            detalhes["fonte"] = (
                f"MIMIC-IV Demo (subject_id {cfg.mimic_subject_id}, "
                f"últimas {cfg.mimic_janela_horas:g} h)"
            )
            return carregar_replay_mimic(
                cfg.mimic_path,
                cfg.mimic_subject_id,
                cfg.mimic_janela_horas,
                contexto.paciente_id,
                agora - timedelta(minutes=5),
            )
        detalhes["fonte"] = "série sintética"
        return gerar_serie_streaming_clinica(contexto.paciente_id, inicio_sim)
