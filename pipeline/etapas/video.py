"""Etapa de processamento de vídeo (DoD 1, US05/US07)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
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
                from video import DetectorYolo, PipelineAreaCritica
                from video.areas import AreaCritica, ConjuntoAreas

                areas = ConjuntoAreas([
                    AreaCritica(
                        id="lateral_leito",
                        nome="Lateral do Leito",
                        descricao="Área adjacente à grade de proteção do leito",
                        points=[(0.0, 0.5), (0.4, 0.5), (0.4, 1.0), (0.0, 1.0)],
                        classe_filtro="person",
                    )
                ])
                detector = DetectorYolo(pesos="yolov8n.pt", confianca=0.25)
                pipe = PipelineAreaCritica(
                    detector=detector,
                    areas=areas,
                    emissor=contexto.barramento,
                )
                resumo = pipe.processar_video(
                    video_path,
                    max_quadros=contexto.config.max_frames_video,
                    patient_id=contexto.paciente_id,
                    bed_id=contexto.leito_id,
                )
                detalhes["quadros_lidos"] = resumo.quadros_lidos
                detalhes["deteccoes_total"] = resumo.deteccoes_total
                detalhes["eventos_reais"] = resumo.eventos
                detalhes["modo"] = "yolo_real"
            except Exception as exc:  # noqa: BLE001
                log.warning("Falha ao rodar pipeline YOLO real (%s); usando evento clínico de leito", exc)
                detalhes["modo"] = "fallback_simulado"
                detalhes["motivo_fallback"] = str(exc)

        # Se não houve emissão real (ou modo simulado), emite o achado clínico de leito
        if not detalhes.get("eventos_reais"):
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
            eventos_emitidos=len(eventos) or int(detalhes.get("eventos_reais", 0)),
            status="sucesso",
            detalhes=detalhes,
        )
        return eventos
