"""Vídeo de leito como série de movimentação: US06 (pose), US07 (áreas), US08 (quedas).

A US17 não roda OpenPose nem chama a US07/US08: lê o que elas gravam.

==============================  ================================================
Fonte                           Observação
==============================  ================================================
pose da US06 (JSON)             posição por janela de ``video.janela_s``
                                (``video_pose``)
eventos da US07 (JSONL)         ``person`` entra na ROI ``area_fora_do_leito``
                                → ``FORA_DO_LEITO`` (``video_areas``)
eventos da US08 (JSONL)         ``mudanca_brusca_ou_queda`` com regra ``queda``
                                → ``CHAO`` + queda (``video_pose``)
caixa ``person`` do YOLOv8      queda pela forma da caixa (``video_caixa``)
==============================  ================================================

Postura (convenções de ângulo da US06, ``video/pose/angles.py``): tronco
pescoço→quadril quase vertical = sentado/em pé; inclinado = deitado. Deitado
com os ombros sobrepostos na imagem (largura dos ombros pequena perto do
tronco) = decúbito lateral; o lado sai de que lado do eixo do tronco o nariz
está, na imagem. Tudo 2D, então depende da posição da câmera.

A queda pela caixa existe porque o relatório da US08 mediu recall 0,23 para
queda só por pose no FallVision: quando o OpenPose perde a pessoa na descida, o
YOLO costuma continuar vendo a caixa, que fica baixa e larga.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path
from statistics import median

from contratos import EventoAchado
from video.detector import Detector
from video.leitor_video import LeitorVideo
from video.modelos import Deteccao
from video.pose.angles import hip_center, inclination_from_vertical
from video.pose.sequence import Keypoint, PoseSequence

from .config import ConfigMovimentacao, Video
from .modelos import Observacao, Posicao

QUEDA_US08 = "mudanca_brusca_ou_queda"

CaixaNoTempo = tuple[float, float, float, float, float]
"""``(tempo_s, x1, y1, x2, y2)`` da maior pessoa no quadro."""


def _dist(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def posicao_de_keypoints(kp: dict[str, Keypoint], cfg: Video) -> Posicao:
    pescoco, quadril = kp.get("Neck"), hip_center(kp)
    inclinacao = inclination_from_vertical(quadril, pescoco)
    if math.isnan(inclinacao):
        return Posicao.DESCONHECIDA
    if inclinacao < cfg.inclinacao_sentado_max:
        return Posicao.SENTADO
    ombro_e, ombro_d = kp.get("LShoulder"), kp.get("RShoulder")
    tronco = _dist(pescoco, quadril)  # type: ignore[arg-type]
    if ombro_e is None or ombro_d is None or tronco == 0:
        return Posicao.DESCONHECIDA
    if _dist(ombro_e, ombro_d) / tronco >= cfg.razao_lateral_max:
        return Posicao.SUPINO
    nariz = kp.get("Nose")
    if nariz is None:
        return Posicao.DESCONHECIDA
    # Produto vetorial (quadril→pescoço) x (pescoço→nariz): de que lado do tronco está o rosto.
    tx, ty = pescoco[0] - quadril[0], pescoco[1] - quadril[1]  # type: ignore[index]
    nx, ny = nariz[0] - pescoco[0], nariz[1] - pescoco[1]  # type: ignore[index]
    lado = tx * ny - ty * nx
    if lado == 0:
        return Posicao.DESCONHECIDA
    return Posicao.LATERAL_ESQ if lado > 0 else Posicao.LATERAL_DIR


def observacoes_de_pose(
    seq: PoseSequence, inicio_utc: datetime, cfg: ConfigMovimentacao
) -> list[Observacao]:
    """Uma observação por janela: a posição conhecida mais frequente nela."""
    janelas: dict[int, Counter[Posicao]] = defaultdict(Counter)
    for frame in seq.frames:
        janela = int(frame.timestamp_ms / 1000 // cfg.video.janela_s)
        janelas[janela][posicao_de_keypoints(frame.keypoints, cfg.video)] += 1
    observacoes = []
    for janela, contagem in sorted(janelas.items()):
        conhecidas = Counter({p: n for p, n in contagem.items() if p is not Posicao.DESCONHECIDA})
        posicao = conhecidas.most_common(1)[0][0] if conhecidas else Posicao.DESCONHECIDA
        instante = inicio_utc + timedelta(seconds=janela * cfg.video.janela_s)
        evidencia = {"video_id": seq.video_id, "quadros": sum(contagem.values())}
        observacoes.append(Observacao(instante, posicao, "video_pose", evidencia=evidencia))
    return observacoes


def observacoes_de_areas(
    eventos: Iterable[EventoAchado], cfg: ConfigMovimentacao
) -> list[Observacao]:
    """``person`` entrando na ROI de fora do leito (US07) = saída do leito."""
    observacoes = []
    for e in eventos:
        f = e.evidence.features
        if (
            e.event_type == "entrada_area_critica"
            and f.get("classe") == "person"
            and f.get("area_critica_id") == cfg.video.area_fora_do_leito
        ):
            evidencia = {"event_id": str(e.event_id), "area": f.get("area_critica_id")}
            observacoes.append(
                Observacao(e.timestamp, Posicao.FORA_DO_LEITO, "video_areas", evidencia=evidencia)
            )
    return observacoes


def observacoes_de_quedas_us08(eventos: Iterable[EventoAchado]) -> list[Observacao]:
    return [
        Observacao(
            e.timestamp, Posicao.CHAO, "video_pose", queda=True,
            evidencia={"event_id": str(e.event_id)},
        )
        for e in eventos
        if e.event_type == QUEDA_US08 and e.evidence.features.get("regra") == "queda"
    ]


# --------------------------------------------------------------------------- #
# Queda pela caixa da pessoa
# --------------------------------------------------------------------------- #


def caixas_de_deteccoes(deteccoes: Iterable[Deteccao]) -> list[CaixaNoTempo]:
    """Maior caixa ``person`` de cada quadro, em ordem de tempo."""
    maiores: dict[int, Deteccao] = {}
    for d in deteccoes:
        if d.class_name != "person":
            continue
        atual = maiores.get(d.frame_index)
        if atual is None or _area(d) > _area(atual):
            maiores[d.frame_index] = d
    return [
        (d.tempo_s, d.caixa.x1, d.caixa.y1, d.caixa.x2, d.caixa.y2)
        for _, d in sorted(maiores.items())
    ]


def _area(d: Deteccao) -> float:
    return (d.caixa.x2 - d.caixa.x1) * (d.caixa.y2 - d.caixa.y1)


def caixas_do_video(
    caminho: str | Path, detector: Detector, max_quadros: int | None = None
) -> list[CaixaNoTempo]:
    """Roda o detector da US07 em cada quadro e devolve a maior pessoa por quadro."""
    deteccoes: list[Deteccao] = []
    with LeitorVideo(caminho, max_quadros=max_quadros) as leitor:
        for q in leitor:
            deteccoes += detector.detectar(q.imagem, indice_quadro=q.indice, tempo_s=q.tempo_s)
    return caixas_de_deteccoes(deteccoes)


def quedas_por_caixa(caixas: Sequence[CaixaNoTempo], cfg: Video) -> list[float]:
    """Instantes (s) em que a pessoa caiu, pela forma da caixa.

    Queda no quadro *i*: a altura da caixa ficou ≤ ``(1 - queda_altura_frac)``
    da maior altura no ``tempo_max_s`` anterior, a caixa ficou com
    largura/altura ≥ ``razao_deitado``, e as duas coisas se mantêm em todos os
    quadros dos ``janela_s`` seguintes. Depois de uma queda, só conta outra
    quando a caixa voltar a ficar em pé.
    """
    regra = cfg.queda_caixa
    if len(caixas) < 2:
        return []
    tempos = [c[0] for c in caixas]
    alturas = [c[4] - c[2] for c in caixas]
    larguras = [c[3] - c[1] for c in caixas]
    passo = median(b - a for a, b in pairwise(tempos))
    eps = 1e-6

    def deitada(k: int) -> bool:
        return alturas[k] > 0 and larguras[k] / alturas[k] >= regra.razao_deitado

    quedas: list[float] = []
    caida = False
    for i, t in enumerate(tempos):
        if caida:
            caida = deitada(i)
            continue
        anteriores = [alturas[k] for k in range(i) if tempos[k] >= t - regra.tempo_max_s - eps]
        if not anteriores:
            continue
        teto = (1 - regra.queda_altura_frac) * max(anteriores)
        janela = [k for k in range(i, len(tempos)) if tempos[k] <= t + regra.janela_s + eps]
        cobre = tempos[janela[-1]] >= t + regra.janela_s - passo - eps
        if cobre and all(alturas[k] <= teto and deitada(k) for k in janela):
            quedas.append(t)
            caida = True
    return quedas


def observacoes_de_quedas_caixa(
    quedas_s: Iterable[float], inicio_utc: datetime
) -> list[Observacao]:
    return [
        Observacao(
            inicio_utc + timedelta(seconds=t), Posicao.CHAO, "video_caixa", queda=True,
            evidencia={"tempo_video_s": round(t, 3)},
        )
        for t in quedas_s
    ]


__all__ = [
    "CaixaNoTempo",
    "caixas_de_deteccoes",
    "caixas_do_video",
    "observacoes_de_areas",
    "observacoes_de_pose",
    "observacoes_de_quedas_caixa",
    "observacoes_de_quedas_us08",
    "posicao_de_keypoints",
    "quedas_por_caixa",
]
