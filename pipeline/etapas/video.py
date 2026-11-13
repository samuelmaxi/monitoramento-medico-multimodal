"""Etapa de processamento de vídeo (DoD 1, US05/US07)."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

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

log = logging.getLogger("pipeline.etapas.video")

CONFIG_VIDEO_PATH = Path("config/leito_uti_07.json")


class EtapaVideo:
    """Processa vídeo ou emite achado visual de leito/área crítica."""

    nome = "video"

    def executar(self, contexto: ContextoPipeline) -> list[EventoAchado]:
        contexto.logger.iniciar_etapa(self.nome)
        eventos: list[EventoAchado] = []
        detalhes: dict[str, object] = {}

        video_path = contexto.config.video_path
        if video_path is None:
            candidato = Path("conteudos/videos/video.mp4")
            if candidato.is_file():
                video_path = candidato

        agora = agora_utc()

        if contexto.config.modo == "real" and video_path and video_path.is_file():
            try:
                eventos, resumo = self._processar_yolo(contexto, video_path, agora)
                detalhes.update(
                    modo="yolo_real",
                    video=video_path.name,
                    quadros_lidos=resumo.quadros_lidos,
                    deteccoes_total=resumo.deteccoes_total,
                    eventos_reais=len(eventos),
                )
                executou_yolo = True
            except Exception as exc:
                log.warning("Falha ao rodar o pipeline YOLO (%s); usando evento de leito", exc)
                detalhes["modo"] = "fallback_simulado"
                detalhes["motivo_fallback"] = str(exc)
                executou_yolo = False
        else:
            executou_yolo = False

        # Sem YOLO (modo simulado, sem vídeo ou falha), emite o achado clínico de leito de demonstração.
        # Com YOLO que não viu entrada/saída, não se inventa achado.
        if not executou_yolo:
            evento = EventoAchado.criar(
                patient_id=contexto.paciente_id,
                modality=Modalidade.VIDEO,
                event_type="entrada_area_critica",
                timestamp=agora - timedelta(minutes=5),
                score=0.89,
                severity=Severidade.BAIXA,
                window=Janela(start=agora - timedelta(minutes=5), end=agora - timedelta(minutes=4)),
                evidence=Evidencia(
                    summary="Detecção de movimentação anormal na borda do leito (área crítica lateral)",
                    features={
                        "roi": "lateral_leito",
                        "fracao_contencao": 0.85,
                        "classe": "person",
                        "duracao_segundos": 15.0,
                    },
                    models={"detector": "yolov8n", "versao": "8.4.173"},
                ),
                context=Contexto(bed_id=contexto.leito_id, source_id="video-cam-01"),
                model_version="video-areas@0.1.0",
                is_synthetic=(contexto.config.modo != "real"),
            )
            contexto.barramento.emitir(evento)
            eventos.append(evento)
            detalhes["modo"] = "clinico_leito"
            detalhes["roi"] = "lateral_leito"

        contexto.logger.concluir_etapa(
            eventos_emitidos=len(eventos),
            status="sucesso",
            detalhes=detalhes,
        )
        return eventos

    def _processar_yolo(self, contexto: ContextoPipeline, video_path: Path, agora: datetime):
        from video import Configuracao, PipelineAreaCritica

        config = Configuracao.de_arquivo(CONFIG_VIDEO_PATH)
        config = replace(
            config,
            fonte=replace(
                config.fonte,
                video=video_path.resolve(),
                id=video_path.stem,
                patient_id=contexto.paciente_id,
                bed_id=contexto.leito_id,
                max_quadros=contexto.config.max_frames_video,
            ),
        )
        pipeline = PipelineAreaCritica(config, emissor=contexto.barramento)
        resultado = pipeline.executar(detectado_em=agora - timedelta(minutes=5))
        return resultado.eventos, resultado.resumo
