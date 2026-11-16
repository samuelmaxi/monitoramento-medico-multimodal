"""Etapa de processamento de vídeo (DoD 1, US05/US07).

Modo real: o YOLOv8 da US07 no vídeo enviado; sem vídeo, a etapa fica ausente, e se o YOLO falha
a etapa falha (nenhum achado de demonstração no lugar). Modo simulado: o achado de leito da demo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
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
# Fixo, nunca derivado do nome do arquivo enviado (que pode trazer o nome do paciente).
SOURCE_ID_VIDEO = "video_leito"
# O quadro 0 do vídeo é situado este tempo antes da execução (a gravação não traz o instante).
ATRASO_VIDEO = timedelta(minutes=5)
# Perfil da US08 aplicado ao vídeo do pipeline: as ROIs dele (borda_leito, lateral_leito) são
# as de CONFIG_VIDEO_PATH. Trocou um, troque o outro.
PERFIL_US08 = "internacao_leito"
CHAVE_VIDEO = "video"  # contexto.dados[CHAVE_VIDEO]: VideoAnalisado, só se o YOLO rodou


@dataclass(frozen=True)
class VideoAnalisado:
    """O que o YOLO (US07) produziu, para a etapa do relatório de desvios (US09)."""

    video_path: Path
    eventos_areas: tuple[EventoAchado, ...]
    quadros_lidos: int
    fps: float | None
    inicio_utc: datetime  # instante do quadro 0

    @property
    def fim_analisado_s(self) -> float | None:
        """Fim do trecho que o YOLO leu (o pipeline limita os quadros, não o vídeo)."""
        if not self.fps or self.fps <= 0 or self.quadros_lidos <= 0:
            return None
        return self.quadros_lidos / self.fps


class EtapaVideo:
    """Processa vídeo ou emite achado visual de leito/área crítica."""

    nome = "video"

    def executar(self, contexto: ContextoPipeline) -> list[EventoAchado]:
        video_path = contexto.config.video_path
        tem_video = video_path is not None and video_path.is_file()
        if contexto.modo_real and not tem_video:
            contexto.logger.ausentar_etapa(self.nome, "sem vídeo enviado")
            return []

        contexto.logger.iniciar_etapa(self.nome)
        agora = agora_utc()
        if not contexto.modo_real:
            return self._demonstracao(contexto, agora)

        try:
            eventos, resumo = self._processar_yolo(contexto, video_path, agora)
        except Exception as exc:
            # Modo real: falha é falha. Nenhum achado de demonstração no lugar do vídeo.
            log.warning("YOLO falhou no vídeo enviado: %s", exc)
            contexto.logger.concluir_etapa(
                status="falha",
                detalhes={"modo": "yolo_real", "erro": f"{type(exc).__name__}: {exc}"},
            )
            return []

        contexto.dados[CHAVE_VIDEO] = VideoAnalisado(
            video_path=video_path,
            eventos_areas=tuple(eventos),
            quadros_lidos=int(resumo.quadros_lidos),
            fps=getattr(resumo, "fps", None),
            inicio_utc=agora - ATRASO_VIDEO,
        )
        contexto.logger.concluir_etapa(
            eventos_emitidos=len(eventos),
            status="sucesso",
            detalhes={
                "modo": "yolo_real",
                "video": video_path.name,
                "quadros_lidos": resumo.quadros_lidos,
                "deteccoes_total": resumo.deteccoes_total,
                "eventos_reais": len(eventos),
            },
        )
        return eventos

    def _demonstracao(self, contexto: ContextoPipeline, agora: datetime) -> list[EventoAchado]:
        """Modo simulado: o achado de leito do cenário da demo (sempre ``is_synthetic``)."""
        evento = EventoAchado.criar(
            patient_id=contexto.paciente_id,
            modality=Modalidade.VIDEO,
            event_type="entrada_area_critica",
            timestamp=agora - ATRASO_VIDEO,
            score=0.89,
            severity=Severidade.BAIXA,
            window=Janela(start=agora - ATRASO_VIDEO, end=agora - timedelta(minutes=4)),
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
            is_synthetic=True,  # achado de demonstração: nunca é medição real
        )
        contexto.barramento.emitir(evento)
        contexto.logger.concluir_etapa(
            eventos_emitidos=1,
            status="sucesso",
            detalhes={"modo": "clinico_leito", "roi": "lateral_leito"},
        )
        return [evento]

    def _processar_yolo(self, contexto: ContextoPipeline, video_path: Path, agora: datetime):
        from video import Configuracao, PipelineAreaCritica

        config = Configuracao.de_arquivo(CONFIG_VIDEO_PATH)
        config = replace(
            config,
            fonte=replace(
                config.fonte,
                video=video_path.resolve(),
                id=SOURCE_ID_VIDEO,
                patient_id=contexto.paciente_id,
                bed_id=contexto.leito_id,
                max_quadros=contexto.config.max_frames_video,
                # O inicio_utc da config é o da gravação de exemplo (2026-10-05). Mantido, ele
                # dataria os achados semanas atrás, fora da janela da fusão. O quadro 0 é o
                # detectado_em abaixo, o mesmo instante que relatório e movimentação usam.
                inicio_utc=None,
            ),
        )
        pipeline = PipelineAreaCritica(config, emissor=contexto.barramento)
        resultado = pipeline.executar(detectado_em=agora - ATRASO_VIDEO)
        return resultado.eventos, resultado.resumo
