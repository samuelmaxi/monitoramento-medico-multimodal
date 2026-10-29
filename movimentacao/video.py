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
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
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


@dataclass(frozen=True)
class CaixasDoVideo:
    caixas: list[CaixaNoTempo]
    altura: int
    largura: int
    fps: float


def caixas_do_video(
    caminho: str | Path,
    detector: Detector,
    *,
    max_quadros: int | None = None,
    fps_max: float | None = None,
) -> CaixasDoVideo:
    """Detector da US07 nos quadros (até ``fps_max`` por segundo); a maior pessoa de cada um."""
    deteccoes: list[Deteccao] = []
    with LeitorVideo(caminho, max_quadros=max_quadros) as leitor:
        meta = leitor.metadados
        passo = max(1, round(meta.fps / fps_max)) if fps_max else 1
        for q in leitor:
            if q.indice % passo == 0:
                deteccoes += detector.detectar(q.imagem, indice_quadro=q.indice, tempo_s=q.tempo_s)
    return CaixasDoVideo(caixas_de_deteccoes(deteccoes), meta.altura, meta.largura, meta.fps)


def detector_padrao(cfg: Video) -> Detector:
    """O ``DetectorYolo`` da US07 com os parâmetros de ``video.detector`` (sem rastreamento)."""
    from video.detector import ConfiguracaoDetector, DetectorYolo

    c = cfg.detector
    config = ConfiguracaoDetector(confianca=c.confianca, imgsz=c.imgsz, classes=("person",))
    return DetectorYolo(config, rastrear=False)


_EPS = 1e-6


def _de(tempos: Sequence[float], t: float) -> int:
    """Primeiro índice com tempo >= t (tolerância de arredondamento)."""
    return bisect_left(tempos, t - _EPS)


def _ate(tempos: Sequence[float], t: float) -> int:
    """Um depois do último índice com tempo <= t."""
    return bisect_right(tempos, t + _EPS)


def quedas_por_descida(
    caixas: Sequence[CaixaNoTempo], altura_quadro: float, cfg: Video
) -> list[float]:
    """Instantes (s) em que a pessoa caiu do leito: o centro da caixa desceu e ficou.

    ``caixas`` em ordem de tempo, como :func:`caixas_de_deteccoes` devolve.

    O centro vertical da caixa (fração da altura do quadro) passa por uma
    mediana móvel de ``suavizacao_s``. Queda no instante *t*: o centro está
    ``descida_min_frac`` abaixo do ponto mais alto dos ``tempo_max_s``
    anteriores e não volta acima dessa marca nos ``janela_s`` seguintes (o que
    houver deles, se o clipe acabar antes). Depois de uma queda, a referência
    da próxima só usa quadros a partir de ``tempo_max_s`` depois dela.
    """
    regra = cfg.queda_descida
    if not caixas or altura_quadro <= 0:
        return []
    tempos = [c[0] for c in caixas]
    centros = [(c[2] + c[4]) / 2 / altura_quadro for c in caixas]
    meia = regra.suavizacao_s / 2
    # Janelas localizadas por bisect: O(quadros x quadros por janela), não O(quadros²),
    # para aguentar horas de câmera de leito.
    suave = [median(centros[_de(tempos, t - meia) : _ate(tempos, t + meia)]) for t in tempos]
    quedas: list[float] = []
    for i, t in enumerate(tempos):
        # A referência nunca volta para antes da queda anterior (a pessoa já está embaixo).
        desde = max(t - regra.tempo_max_s, quedas[-1] + regra.tempo_max_s if quedas else -math.inf)
        antes = suave[_de(tempos, desde) : i]
        if not antes:
            continue
        topo = min(antes)  # y cresce para baixo: o ponto mais alto é o menor y
        depois = suave[i : _ate(tempos, t + regra.janela_s)]
        if min(depois) - topo >= regra.descida_min_frac:
            quedas.append(t)
    return quedas


def quedas_por_caixa(caixas: Sequence[CaixaNoTempo], cfg: Video) -> list[float]:
    """Instantes (s) em que a pessoa caiu, pela forma da caixa.

    Queda no quadro *i*: a caixa mais alta do ``tempo_max_s`` anterior estava
    em pé (mais alta que larga), a altura caiu para ≤ ``(1 - queda_altura_frac)``
    dela, a caixa ficou com
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

    def deitada(k: int) -> bool:
        return alturas[k] > 0 and larguras[k] / alturas[k] >= regra.razao_deitado

    quedas: list[float] = []
    caida = False
    for i, t in enumerate(tempos):
        if caida:
            caida = deitada(i)
            continue
        anteriores = range(_de(tempos, t - regra.tempo_max_s), i)
        if not anteriores:
            continue
        ref = max(anteriores, key=lambda k: alturas[k])
        if deitada(ref):  # já estava deitada: não há queda de pé para medir
            continue
        teto = (1 - regra.queda_altura_frac) * alturas[ref]
        janela = range(i, _ate(tempos, t + regra.janela_s))
        cobre = tempos[janela[-1]] >= t + regra.janela_s - passo - _EPS
        if cobre and all(alturas[k] <= teto and deitada(k) for k in janela):
            quedas.append(t)
            caida = True
    return quedas


REGRAS_QUEDA_PADRAO = ("descida",)
"""Regras de queda pela caixa usadas fora da avaliação. Escolhidas na partição de
calibração (``scripts/avaliar_us17.py``): ``caixa`` não aumentou o recall nos clipes
de leito e acrescentou alarme falso."""


def observacoes_de_caixas(
    caixas: CaixasDoVideo,
    inicio_utc: datetime,
    cfg: Video,
    regras: Iterable[str] = REGRAS_QUEDA_PADRAO,
) -> list[Observacao]:
    """Quedas pelas regras de caixa pedidas (``descida`` e/ou ``caixa``) como observações."""
    instantes: list[tuple[float, str]] = []
    if "descida" in regras:
        descidas = quedas_por_descida(caixas.caixas, caixas.altura, cfg)
        instantes += [(t, "video_descida") for t in descidas]
    if "caixa" in regras:
        instantes += [(t, "video_caixa") for t in quedas_por_caixa(caixas.caixas, cfg)]
    return [
        Observacao(
            inicio_utc + timedelta(seconds=t), Posicao.CHAO, fonte, queda=True,
            evidencia={"tempo_video_s": round(t, 3)},
        )
        for t, fonte in instantes
    ]


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
    "REGRAS_QUEDA_PADRAO",
    "CaixaNoTempo",
    "CaixasDoVideo",
    "caixas_de_deteccoes",
    "caixas_do_video",
    "detector_padrao",
    "observacoes_de_areas",
    "observacoes_de_caixas",
    "observacoes_de_pose",
    "observacoes_de_quedas_caixa",
    "observacoes_de_quedas_us08",
    "posicao_de_keypoints",
    "quedas_por_caixa",
    "quedas_por_descida",
]
