import logging
import math
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.video.config import SUPPORTED_EXTENSIONS
from app.video.errors import CorruptedVideoError, InvalidVideoFormatError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Frame:
    frame_index: int
    timestamp_ms: int
    image: np.ndarray


class VideoReader:
    """Lê um vídeo frame a frame, com timestamp em ms derivado do fps da origem.

    Usado como context manager para garantir a liberação do VideoCapture.
    Após iterar, `partial` indica se o vídeo parou de entregar frames antes do
    total declarado nos metadados.
    """

    def __init__(
        self,
        path: Path | str,
        supported_extensions: tuple[str, ...] = SUPPORTED_EXTENSIONS,
    ) -> None:
        self.path = Path(path)
        self._supported_extensions = supported_extensions
        self._capture: cv2.VideoCapture | None = None
        self.source_fps: float = 0.0
        self.declared_frame_count: int = 0
        self.frames_read: int = 0
        self.partial: bool = False

    def __enter__(self) -> "VideoReader":
        self._validate_extension()
        if not self.path.is_file():
            raise CorruptedVideoError(f"Arquivo não encontrado: {self.path}")

        capture = cv2.VideoCapture(str(self.path))
        if not capture.isOpened():
            capture.release()
            raise CorruptedVideoError(f"Não foi possível abrir o vídeo: {self.path}")

        source_fps = capture.get(cv2.CAP_PROP_FPS)
        if not source_fps or not math.isfinite(source_fps) or source_fps <= 0:
            capture.release()
            raise InvalidVideoFormatError(
                f"FPS de origem inválido ({source_fps}) em {self.path}"
            )

        self._capture = capture
        self.source_fps = float(source_fps)
        self.declared_frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def frames(self) -> Iterator[Frame]:
        if self._capture is None:
            raise RuntimeError("Use VideoReader dentro de um bloco 'with'.")

        frame_index = 0
        while True:
            ok, image = self._capture.read()
            if not ok:
                break
            yield Frame(
                frame_index=frame_index,
                timestamp_ms=round(frame_index / self.source_fps * 1000),
                image=image,
            )
            frame_index += 1

        self.frames_read = frame_index
        if self.declared_frame_count > 0 and frame_index < self.declared_frame_count:
            self.partial = True
            logger.warning(
                "Vídeo %s parou de entregar frames: lidos %d de %d declarados.",
                self.path,
                frame_index,
                self.declared_frame_count,
            )

    def _validate_extension(self) -> None:
        if self.path.suffix.lower() not in self._supported_extensions:
            raise InvalidVideoFormatError(
                f"Extensão não suportada '{self.path.suffix}'. "
                f"Aceitas: {', '.join(self._supported_extensions)}"
            )
