"""Etapa de movimentação: imobilidade e quedas pelas regras da US17 (DoD 1).

Modo real: a série vem do vídeo enviado, como no ``python -m movimentacao.run video``:

- queda pela caixa ``person`` do YOLOv8 (não precisa de OpenPose; ``movimentacao.video``);
- saída do leito pelos eventos de área crítica que a etapa de vídeo (US07) já produziu;
- queda da US08, se a etapa do relatório de vídeo tiver encontrado uma (só com pose).

Sem vídeo, a etapa fica ausente: o pipeline não recebe registro de enfermagem do paciente.
Modo simulado: o cenário de demonstração (190 min em supino).
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from contratos import EventoAchado, agora_utc
from movimentacao import video as fonte
from movimentacao.config import ConfigMovimentacao, carregar_config
from movimentacao.eventos import montar_evento, montar_sem_achados
from movimentacao.modelos import Observacao, Posicao
from movimentacao.regras import detectar
from pipeline.etapas.base import ContextoPipeline
from pipeline.etapas.relatorio_video import CHAVE_RELATORIO
from pipeline.etapas.video import ATRASO_VIDEO, CHAVE_VIDEO, SOURCE_ID_VIDEO, VideoAnalisado

log = logging.getLogger("pipeline.etapas.movimentacao")

INTERVALO_REGISTRO_MIN = 30
IMOBILIDADE_DEMO_MIN = 190


def gerar_observacoes_demo(agora: datetime) -> list[Observacao]:
    """Registro de enfermagem: paciente em supino, sem reposicionamento, por 190 min."""
    return [
        Observacao(agora - timedelta(minutes=m), Posicao.SUPINO, "enfermagem")
        for m in range(IMOBILIDADE_DEMO_MIN, -1, -INTERVALO_REGISTRO_MIN)
    ]


class EtapaMovimentacao:
    """Detecta imobilidade prolongada, saída do leito e queda (regras da US17)."""

    nome = "movimentacao"

    def executar(self, contexto: ContextoPipeline) -> list[EventoAchado]:
        video = contexto.config.video_path
        if contexto.modo_real and (video is None or not video.is_file()):
            contexto.logger.ausentar_etapa(
                self.nome, "sem vídeo enviado (o pipeline não recebe registro de enfermagem)"
            )
            return []

        contexto.logger.iniciar_etapa(self.nome)
        cfg = carregar_config()
        agora = agora_utc()
        if contexto.modo_real:
            serie, detalhes = self._serie_do_video(contexto, video, cfg, agora)
            anomalias = detectar(serie, cfg)
            sintetico = False
            source_id = SOURCE_ID_VIDEO
        else:
            serie = gerar_observacoes_demo(agora)
            anomalias = detectar(serie, cfg, ate=agora)
            sintetico = True
            source_id = f"leito-{contexto.leito_id.lower()}"
            detalhes = {
                "fonte": f"cenário de demonstração (regras da US17, versão {cfg.versao})",
                "observacoes_analisadas": len(serie),
            }

        ids = {
            "paciente": contexto.paciente_id,
            "bed_id": contexto.leito_id,
            "source_id": source_id,
            "sintetico": sintetico,
            "chave": contexto.config.chave_pseudonimizacao,
        }
        eventos = [montar_evento(a, cfg, **ids) for a in anomalias]
        if not eventos and serie:
            sem = {k: v for k, v in ids.items() if k != "sintetico"}
            eventos = [montar_sem_achados(serie, cfg, **sem)]
        for evento in eventos:
            contexto.barramento.emitir(evento)

        detalhes["anomalias"] = [a.event_type for a in anomalias]
        contexto.logger.concluir_etapa(
            eventos_emitidos=len(eventos), status="sucesso", detalhes=detalhes
        )
        return eventos

    def _serie_do_video(
        self, contexto: ContextoPipeline, video: Path, cfg: ConfigMovimentacao, agora: datetime
    ) -> tuple[list[Observacao], dict[str, Any]]:
        analisado: VideoAnalisado | None = contexto.dados.get(CHAVE_VIDEO)
        # O mesmo instante de quadro 0 que a etapa de vídeo usou, para os achados se alinharem.
        inicio = analisado.inicio_utc if analisado else agora - ATRASO_VIDEO

        caixas = self._caixas_do_video(video, cfg, contexto.config.max_frames_video)
        serie = fonte.observacoes_de_caixas(caixas, inicio, cfg.video)
        if analisado is not None:
            serie += fonte.observacoes_de_areas(analisado.eventos_areas, cfg)
        relatorio = contexto.dados.get(CHAVE_RELATORIO)
        if relatorio is not None:
            serie += fonte.observacoes_de_quedas_us08(relatorio.execucao.eventos)

        detalhes: dict[str, Any] = {
            "fonte": f"vídeo enviado (regras da US17, versão {cfg.versao})",
            "quadros_com_pessoa": len(caixas.caixas),
            "observacoes_por_fonte": dict(Counter(o.fonte for o in serie)),
            "quedas_observadas": sum(1 for o in serie if o.queda),
        }
        if not serie:
            detalhes["motivo"] = (
                "nenhuma queda pela caixa da pessoa nem saída do leito no trecho analisado"
            )
        return serie, detalhes

    def _caixas_do_video(
        self, video: Path, cfg: ConfigMovimentacao, max_quadros: int | None
    ) -> fonte.CaixasDoVideo:
        """YOLOv8 (só ``person``, sem rastreamento) nos quadros, até ``video.detector.fps_max``."""
        return fonte.caixas_do_video(
            video,
            fonte.detector_padrao(cfg.video),
            max_quadros=max_quadros,
            fps_max=cfg.video.detector.fps_max,
        )
