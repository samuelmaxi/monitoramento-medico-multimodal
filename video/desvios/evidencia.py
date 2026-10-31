"""Quadro de evidência de cada desvio: o frame do pico com esqueleto e legenda.

Usa o desenho da US06 (``video.pose.visualize``) para o esqueleto e desenha a
caixa da detecção quando o desvio vem de área crítica (US07). O JPG vira um
``Artefato(kind="frame")`` no evento, com caminho relativo à raiz do repositório.

O quadro sai anonimizado (LGPD). Com pose, a cabeça inteira é tarjada a partir
dos keypoints do rosto, o que cobre o rosto de perfil ou pequeno; sem pose
(desvio de área crítica), vale o detector YuNet da US05 (``app.video.anonymizer``),
cujos falsos positivos com limiar baixo escondiam a própria evidência.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from pathlib import Path

import cv2
import numpy as np

from contratos import Artefato
from video.pose.config import VisualizationConfig
from video.pose.sequence import Keypoint, PoseSequence
from video.pose.visualize import _ascii, _put_label, draw_skeleton, read_frames, skeleton_edges

from .modelos import Desvio

log = logging.getLogger(__name__)

_COR_CAIXA = (0, 200, 255)
_CABECA = ("Nose", "LEye", "REye", "LEar", "REar")


def _tarjar_cabeca(imagem: np.ndarray, keypoints: dict[str, Keypoint]) -> None:
    """Tarja preta na cabeça, estimada pelos keypoints do rosto e pela distância ao pescoço."""
    pontos = [keypoints[n] for n in _CABECA if keypoints.get(n) is not None]
    if not pontos:
        return
    xs, ys = [p[0] for p in pontos], [p[1] for p in pontos]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    raio = max(max(xs) - min(xs), max(ys) - min(ys)) / 2
    pescoco = keypoints.get("Neck")
    if pescoco is not None:
        raio = max(raio, 0.8 * float(np.hypot(pescoco[0] - cx, pescoco[1] - cy)))
    raio = max(raio * 1.8, imagem.shape[0] / 40)
    altura, largura = imagem.shape[:2]
    x1, y1 = max(0, int(cx - raio)), max(0, int(cy - raio))
    x2, y2 = min(largura, int(cx + raio)), min(altura, int(cy + raio))
    imagem[y1:y2, x1:x2] = 0


def _nome_arquivo(indice: int, desvio: Desvio) -> str:
    regra = re.sub(r"[^A-Za-z0-9_.-]", "_", desvio.regra)
    return f"{indice:02d}_{regra}_{desvio.pico_s:07.2f}s.jpg"


def salvar_evidencias(
    desvios: Sequence[Desvio],
    *,
    video: Path,
    destino: Path,
    vis: VisualizationConfig,
    pose: PoseSequence | None = None,
    raiz: Path | None = None,
    anonimizar: bool = True,
) -> dict[int, Artefato]:
    """Grava um JPG por desvio com ``frame_idx`` e devolve ``{índice do desvio: Artefato}``.

    O URI é relativo a ``raiz`` (padrão: diretório atual), exigência do
    contrato; um destino fora da raiz grava o arquivo mas não gera artefato.
    """
    pedidos = {i: d for i, d in enumerate(desvios) if d.frame_idx is not None}
    if not pedidos:
        return {}
    imagens = read_frames(video, sorted({d.frame_idx for d in pedidos.values()}))
    keypoints = {f.frame_idx: f.keypoints for f in pose.frames} if pose is not None else {}
    arestas = skeleton_edges(pose.metadata["model"]) if pose is not None else []
    raiz = (raiz or Path.cwd()).resolve()
    destino.mkdir(parents=True, exist_ok=True)
    anonimizador = None
    if anonimizar:
        from app.video.anonymizer import FaceAnonymizer

        anonimizador = FaceAnonymizer(method="fill", memory_frames=0)

    artefatos: dict[int, Artefato] = {}
    for i, desvio in pedidos.items():
        imagem = imagens.get(desvio.frame_idx)
        if imagem is None:
            log.warning("quadro %d não pôde ser lido de %s", desvio.frame_idx, video)
            continue
        if anonimizador is not None:
            if desvio.frame_idx in keypoints:
                _tarjar_cabeca(imagem, keypoints[desvio.frame_idx])
            else:
                imagem = anonimizador.apply(imagem)
        if desvio.frame_idx in keypoints:
            imagem = draw_skeleton(imagem, keypoints[desvio.frame_idx], arestas, vis)
        caixa = [desvio.features.get(k) for k in ("x1", "y1", "x2", "y2")]
        if all(v is not None for v in caixa):
            x1, y1, x2, y2 = (int(v) for v in caixa)
            espessura = max(2, imagem.shape[0] // 300)
            cv2.rectangle(imagem, (x1, y1), (x2, y2), _COR_CAIXA, espessura, cv2.LINE_AA)
        _put_label(
            imagem,
            [
                _ascii(f"US08 {desvio.event_type} | {desvio.regra}"),
                _ascii(
                    f"t = {desvio.pico_s:.2f} s | quadro {desvio.frame_idx} | "
                    f"{desvio.medida} = {desvio.valor:g} (limiar {desvio.limiar:g})"
                ),
            ],
        )
        caminho = destino / _nome_arquivo(i, desvio)
        cv2.imwrite(str(caminho), imagem)
        try:
            uri = caminho.resolve().relative_to(raiz).as_posix()
        except ValueError:
            log.warning("evidência %s fora da raiz %s; sem artefato no evento", caminho, raiz)
            continue
        artefatos[i] = Artefato(
            kind="frame",
            uri=uri,
            media_type="image/jpeg",
            start_ms=int(desvio.inicio_s * 1000),
            end_ms=int(desvio.fim_s * 1000),
        )
    return artefatos
