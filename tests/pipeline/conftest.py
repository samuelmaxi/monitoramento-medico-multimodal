"""Fixtures do pipeline com vídeo "real": vídeo sintético e um YOLO falso (sem pesos nem GPU)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from contratos import Contexto, EventoAchado, Evidencia, Modalidade, Severidade
from movimentacao.video import CaixasDoVideo
from pipeline.etapas.movimentacao import EtapaMovimentacao
from pipeline.etapas.video import EtapaVideo
from video import criar_video_teste

FPS = 10.0
ALTURA, LARGURA = 480, 640

# Pessoa em pé por 1,5 s (centro da caixa a 31% da altura) e depois no chão (73%): a regra de
# queda por descida da caixa da US17 (config/us17_movimentacao.yaml, queda_descida) dispara.
CAIXAS_QUEDA = [(i / FPS, 100.0, 100.0, 200.0, 200.0) for i in range(15)] + [
    (i / FPS, 100.0, 300.0, 300.0, 400.0) for i in range(15, 30)
]


@pytest.fixture(autouse=True)
def caixas_movimentacao(monkeypatch):
    """O YOLO da movimentação (US17) nunca roda nos testes: devolve as caixas configuradas.

    Padrão: nenhuma pessoa vista. ``caixas_movimentacao(CAIXAS_QUEDA)`` simula uma queda.
    """
    atual: list = []

    def falso(self, video, cfg, max_quadros):
        return CaixasDoVideo(list(atual), ALTURA, LARGURA, FPS)

    monkeypatch.setattr(EtapaMovimentacao, "_caixas_do_video", falso)

    def configurar(caixas) -> None:
        atual[:] = caixas

    return configurar


def evento_area(
    contexto, caminho: Path, agora, tipo: str, t: float, area: str = "borda_leito"
) -> EventoAchado:
    """Evento como os da US07: o mesmo formato que ``video.eventos.TradutorEventos`` emite."""
    return EventoAchado.criar(
        patient_id=contexto.paciente_id,
        modality=Modalidade.VIDEO,
        event_type=tipo,
        timestamp=agora + timedelta(seconds=t),
        score=0.8,
        severity=Severidade.BAIXA,
        evidence=Evidencia(
            summary=f"person {tipo}",
            features={
                "classe": "person",
                "area_critica_id": area,
                "area_critica_nome": area.replace("_", " ").capitalize(),
                "tempo_video_s": t,
                "frame_index": int(t * FPS),
                "x1": 10.0,
                "y1": 10.0,
                "x2": 50.0,
                "y2": 90.0,
                "track_id": 3,
            },
            thresholds={"confianca_minima": 0.25, "iou_nms": 0.5, "criterio_roi": "centro"},
            models={"yolov8": "yolov8n@8.4.0"},
        ),
        # A US07 usa o nome do arquivo como source_id: o relatório não pode repeti-lo.
        context=Contexto(source_id=caminho.stem, bed_id=contexto.leito_id),
        model_version="video-areas-criticas@0.1.0",
    )


@pytest.fixture
def video_sintetico(tmp_path):
    """Vídeo de 15 s a 10 fps, com um nome de arquivo que parece de paciente."""

    def criar(nome: str = "paciente_joao_silva.avi", quadros: int = 150) -> Path:
        return criar_video_teste(tmp_path / "entrada" / nome, quadros=quadros, fps=FPS)

    return criar


@pytest.fixture
def yolo_falso(monkeypatch):
    """Troca o YOLO por transições fixas ``[(tipo, t_s, area), ...]`` e ``quadros_lidos``.

    Como o real, publica os eventos no barramento da execução.
    """

    def configurar(transicoes, quadros_lidos: int = 150, fps: float | None = FPS):
        def falso(self, contexto, caminho, agora):
            inicio = agora - timedelta(minutes=5)
            eventos = [
                evento_area(contexto, caminho, inicio, tipo, t, area)
                for tipo, t, area in transicoes
            ]
            contexto.barramento.emitir_varios(eventos)
            resumo = SimpleNamespace(
                quadros_lidos=quadros_lidos, deteccoes_total=len(eventos), fps=fps
            )
            return eventos, resumo

        monkeypatch.setattr(EtapaVideo, "_processar_yolo", falso)

    return configurar
