"""Ângulos articulares por quadro a partir de uma :class:`PoseSequence`.

Convenções:

- Ângulo articular = ângulo interno no vértice, de 0° a 180°. Membro esticado
  dá ~180° (joelho e cotovelo estendidos); flexão = 180° − ângulo.
- Esquerdo/direito são do paciente, como no OpenPose (LKnee = joelho esquerdo).
- Inclinação do tronco = ângulo entre o vetor quadril→pescoço e a vertical
  da imagem: 0° tronco em pé, 90° deitado, 180° de cabeça para baixo.
- Assimetria = |esquerdo − direito|.
- Ângulos são calculados em pixels da imagem (projeção 2D), não em 3D.
- Se algum keypoint necessário for null, o ângulo é NaN.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

import pandas as pd

from .sequence import Keypoint, PoseFrame, PoseSequence

# coluna -> (ponto A, vértice, ponto C)
JOINT_ANGLES: dict[str, tuple[str, str, str]] = {
    "joelho_esquerdo": ("LHip", "LKnee", "LAnkle"),
    "joelho_direito": ("RHip", "RKnee", "RAnkle"),
    "quadril_esquerdo": ("LShoulder", "LHip", "LKnee"),
    "quadril_direito": ("RShoulder", "RHip", "RKnee"),
    "ombro_esquerdo": ("LHip", "LShoulder", "LElbow"),
    "ombro_direito": ("RHip", "RShoulder", "RElbow"),
    "cotovelo_esquerdo": ("LShoulder", "LElbow", "LWrist"),
    "cotovelo_direito": ("RShoulder", "RElbow", "RWrist"),
}

ASYMMETRIES: dict[str, tuple[str, str]] = {
    "assimetria_joelho": ("joelho_esquerdo", "joelho_direito"),
    "assimetria_quadril": ("quadril_esquerdo", "quadril_direito"),
    "assimetria_ombro": ("ombro_esquerdo", "ombro_direito"),
    "assimetria_cotovelo": ("cotovelo_esquerdo", "cotovelo_direito"),
}

TRUNK_COLUMN = "inclinacao_tronco"

ANGLE_COLUMNS: list[str] = [*JOINT_ANGLES, TRUNK_COLUMN, *ASYMMETRIES]

Point = Sequence[float]


def joint_angle(a: Optional[Point], vertex: Optional[Point], c: Optional[Point]) -> float:
    """Ângulo em graus no ``vertex`` entre os segmentos vertex→a e vertex→c.

    Devolve NaN se algum ponto faltar ou se um dos segmentos tiver comprimento zero.
    """
    if a is None or vertex is None or c is None:
        return math.nan
    v1 = (a[0] - vertex[0], a[1] - vertex[1])
    v2 = (c[0] - vertex[0], c[1] - vertex[1])
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 == 0 or n2 == 0:
        return math.nan
    cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def inclination_from_vertical(lower: Optional[Point], upper: Optional[Point]) -> float:
    """Ângulo em graus entre o vetor lower→upper e a vertical para cima.

    Em imagem o eixo y cresce para baixo, então "para cima" é (0, -1).
    """
    if lower is None or upper is None:
        return math.nan
    dx, dy = upper[0] - lower[0], upper[1] - lower[1]
    norm = math.hypot(dx, dy)
    if norm == 0:
        return math.nan
    return math.degrees(math.acos(max(-1.0, min(1.0, -dy / norm))))


def hip_center(keypoints: dict[str, Keypoint]) -> Optional[Point]:
    """MidHip (BODY_25) ou, na falta dele, o ponto médio entre os quadris."""
    mid = keypoints.get("MidHip")
    if mid is not None:
        return mid
    left, right = keypoints.get("LHip"), keypoints.get("RHip")
    if left is None or right is None:
        return None
    return ((left[0] + right[0]) / 2, (left[1] + right[1]) / 2)


def frame_angles(keypoints: dict[str, Keypoint]) -> dict[str, float]:
    row = {
        col: joint_angle(keypoints.get(a), keypoints.get(v), keypoints.get(c))
        for col, (a, v, c) in JOINT_ANGLES.items()
    }
    row[TRUNK_COLUMN] = inclination_from_vertical(hip_center(keypoints), keypoints.get("Neck"))
    for col, (left, right) in ASYMMETRIES.items():
        row[col] = abs(row[left] - row[right])  # NaN se algum lado for NaN
    return row


def compute_angles(seq: PoseSequence) -> pd.DataFrame:
    """Uma linha por quadro processado: frame_idx, timestamp_ms e os ângulos em graus."""
    rows = [_row(f) for f in seq.frames]
    return pd.DataFrame(rows, columns=["frame_idx", "timestamp_ms", *ANGLE_COLUMNS])


def _row(frame: PoseFrame) -> dict[str, float]:
    return {"frame_idx": frame.frame_idx, "timestamp_ms": frame.timestamp_ms, **frame_angles(frame.keypoints)}
