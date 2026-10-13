from dataclasses import asdict, dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    bbox: tuple[int, int, int, int]  # x, y, largura, altura

    def to_dict(self) -> dict:
        data = asdict(self)
        data["bbox"] = list(self.bbox)
        return data


class Detector(Protocol):
    """Ponto de extensão para os modelos de detecção (YOLOv8, OpenPose, ...)."""

    def detect(self, image: np.ndarray) -> list[Detection]: ...


class NullDetector:
    """Stub que não detecta nada. Mantém o formato de saída enquanto o modelo não é plugado."""

    def detect(self, image: np.ndarray) -> list[Detection]:
        return []
