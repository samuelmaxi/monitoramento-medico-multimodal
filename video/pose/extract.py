"""API de extração de pose: ``extract_pose`` e ``compute_angles``.

Uso::

    from video.pose.config import load_config
    from video.pose.extract import extract_pose, compute_angles

    cfg = load_config("video/pose/config.yaml")
    seq = extract_pose("data/rehab24/videos/Ex1/PM_000-Camera17-30fps.mp4", cfg)
    df = compute_angles(seq)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional, Union

import cv2
import numpy as np

from . import __version__
from .angles import compute_angles
from .config import PoseConfig
from .openpose import OpenPoseEstimator
from .sequence import Keypoint, PoseFrame, PoseSequence

__all__ = ["VideoReadError", "compute_angles", "extract_pose", "iter_sampled_frames", "video_id_for"]

log = logging.getLogger(__name__)


class VideoReadError(RuntimeError):
    """Vídeo inexistente, corrompido ou sem quadros legíveis."""


@dataclass(frozen=True)
class VideoInfo:
    source_fps: float
    n_frames: int
    width: int
    height: int
    step: float  # quadros de origem por quadro processado

    @property
    def processed_fps(self) -> float:
        return self.source_fps / self.step


def _open(path: Path) -> cv2.VideoCapture:
    if not path.is_file():
        raise VideoReadError(f"arquivo não encontrado: {path}")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        cap.release()
        raise VideoReadError(f"OpenCV não conseguiu abrir o vídeo: {path}")
    return cap


def _video_info(cap: cv2.VideoCapture, target_fps: float, path: Path) -> VideoInfo:
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or math.isnan(fps) or fps <= 0:
        raise VideoReadError(f"fps inválido ({fps!r}) no vídeo: {path}")
    return VideoInfo(
        source_fps=float(fps),
        n_frames=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        step=max(1.0, fps / target_fps),
    )


def iter_sampled_frames(
    path: Union[str, Path], target_fps: float, max_frames: Optional[int] = None,
) -> Iterator[tuple[int, int, np.ndarray]]:
    """Gera (frame_idx, timestamp_ms, quadro BGR) amostrando ``target_fps`` quadros/s.

    O timestamp é derivado do índice do quadro e do fps do contêiner
    (frame_idx * 1000 / fps), o que é estável entre backends do OpenCV.
    """
    path = Path(path)
    cap = _open(path)
    try:
        info = _video_info(cap, target_fps, path)
        next_keep = 0.0
        idx = 0
        emitted = 0
        while max_frames is None or emitted < max_frames:
            if not cap.grab():
                break
            if idx + 1e-9 >= next_keep:
                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    log.warning("%s: falha ao decodificar o quadro %d; pulando", path, idx)
                else:
                    yield idx, int(round(idx * 1000.0 / info.source_fps)), frame
                    emitted += 1
                next_keep += info.step
            idx += 1
    finally:
        cap.release()


def video_id_for(video_path: Union[str, Path], root: Optional[Union[str, Path]] = None) -> str:
    """Identificador do vídeo: caminho relativo a ``root`` sem extensão, com
    "/" trocado por "__" e espaços por "_".

    O FallVision repete nomes de arquivo entre pastas (B_N_01.mp4 existe em
    várias), então o nome sozinho não é único.
    """
    path = Path(video_path)
    rel = Path(path.stem)
    if root is not None and Path(root).is_dir():
        try:
            rel = path.resolve().relative_to(Path(root).resolve()).with_suffix("")
        except ValueError:
            pass
    return "__".join(rel.parts).replace(" ", "_")


def _apply_threshold(raw, names, threshold: float) -> dict[str, Keypoint]:
    keypoints: dict[str, Keypoint] = {}
    for name, kp in zip(names, raw):
        if kp is None or kp[2] < threshold:
            keypoints[name] = None
        else:
            keypoints[name] = [round(kp[0], 1), round(kp[1], 1), round(kp[2], 3)]
    return keypoints


def extract_pose(
    video_path: Union[str, Path],
    config: PoseConfig,
    *,
    estimator: Optional[OpenPoseEstimator] = None,
    video_id: Optional[str] = None,
) -> PoseSequence:
    """Extrai a pose quadro a quadro de um vídeo.

    ``estimator`` permite reaproveitar o modelo carregado entre vídeos de um
    lote. Levanta :class:`VideoReadError` se o vídeo não puder ser lido.
    """
    path = Path(video_path)
    if estimator is None:
        estimator = OpenPoseEstimator(config.model, config.postprocess)

    cap = _open(path)
    try:
        info = _video_info(cap, config.sampling.target_fps, path)
    finally:
        cap.release()

    names = list(estimator.keypoint_names)
    threshold = config.keypoints.conf_threshold
    frames: list[PoseFrame] = []
    for frame_idx, ts_ms, frame in iter_sampled_frames(path, config.sampling.target_fps, config.sampling.max_frames):
        raw = estimator.estimate(frame)
        frames.append(PoseFrame(frame_idx, ts_ms, _apply_threshold(raw, names, threshold)))
    if not frames:
        raise VideoReadError(f"nenhum quadro legível no vídeo: {path}")

    metadata = {
        "model": config.model.name,
        "model_files": {"prototxt": config.model.prototxt.name, "weights": config.model.weights.name},
        "input_height": config.model.input_height,
        "backend": estimator.backend,
        "source_fps": round(info.source_fps, 3),
        "target_fps": config.sampling.target_fps,
        "processed_fps": round(info.processed_fps, 3),
        "conf_threshold": threshold,
        "min_valid_keypoints": config.keypoints.min_valid_keypoints,
        "frame_width": info.width,
        "frame_height": info.height,
        "n_source_frames": info.n_frames,
        "n_processed_frames": len(frames),
        "coordinates": "pixels do quadro original; keypoint = [x, y, conf] ou null",
        "module_version": __version__,
        "opencv_version": cv2.__version__,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return PoseSequence(
        video_id=video_id or video_id_for(path),
        source_path=str(path),
        keypoint_names=names,
        metadata=metadata,
        frames=frames,
    )
