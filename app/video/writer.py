import json
from pathlib import Path
from types import TracebackType

import cv2
import numpy as np

from app.video.detector import Detection


def write_json(result: dict, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


class VideoFileWriter:
    """Grava frames em MP4 sem nenhuma alteração. Usado para o vídeo anonimizado limpo."""

    def __init__(self, output_path: Path, fps: float, frame_size: tuple[int, int]) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path = output_path
        # 'mp4v' é o codec mais portável no OpenCV headless; H.264 depende do build.
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self._writer = cv2.VideoWriter(str(output_path), fourcc, fps, frame_size)
        if not self._writer.isOpened():
            raise RuntimeError(f"Não foi possível abrir o gravador de vídeo: {output_path}")

    def __enter__(self) -> "VideoFileWriter":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._writer.release()

    def write(self, image: np.ndarray) -> None:
        self._writer.write(image)


class AnnotatedVideoWriter(VideoFileWriter):
    """Grava um MP4 com as caixas de detecção desenhadas sobre os frames já anonimizados.

    Contém apenas os frames amostrados, na taxa `target_fps`, para manter a
    correspondência com o JSON de saída.
    """

    def write_annotated(self, image: np.ndarray, detections: list[Detection]) -> None:
        annotated = image.copy()
        for detection in detections:
            x, y, w, h = detection.bbox
            cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(
                annotated,
                f"{detection.label} {detection.confidence:.2f}",
                (x, max(0, y - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                1,
            )
        self.write(annotated)
