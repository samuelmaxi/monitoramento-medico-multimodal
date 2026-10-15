from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from app.video.config import ANONYMIZE_METHODS, DEFAULT_ANONYMIZE_METHOD

Box = tuple[int, int, int, int]  # x, y, largura, altura

YUNET_MODEL_PATH = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"


class FaceAnonymizer:
    """Oculta as regiões de rosto detectadas pelo YuNet (OpenCV).

    Estratégia orientada a recall: um rosto sem anonimização é um vazamento (LGPD),
    então preferimos ocultar regiões a mais do que deixar um rosto de fora.
    - Detecção com YuNet (`cv2.FaceDetectorYN`), com limiar de score baixo.
    - Cada caixa é expandida pela `padding` para cobrir movimentos da cabeça.
    - Memória de `memory_frames` frames processados: uma caixa que sumiu continua
    oculta nessas posições por um curto período, cobrindo falhas intermitentes.
    - `method`: "fill" (tarja preta, padrão), "pixelate" ou "blur".

    Instância com estado: use uma por vídeo.
    `find_faces` substitui o detector (usado nos testes).
    """

    def __init__(
        self,
        method: str = DEFAULT_ANONYMIZE_METHOD,
        score_threshold: float = 0.5,
        padding: float = 0.15,
        memory_frames: int = 3,
        find_faces: Callable[[np.ndarray], list[Box]] | None = None,
    ) -> None:
        if method not in ANONYMIZE_METHODS:
            raise ValueError(f"method inválido '{method}'. Use um de: {', '.join(ANONYMIZE_METHODS)}.")
        self._method = method
        self._padding = padding
        self._memory_frames = memory_frames
        # (caixa, frames desde a última detecção)
        self._memory: list[tuple[Box, int]] = []
        if find_faces is not None:
            self._find_faces = find_faces
        else:
            self._yunet = self._load_yunet(score_threshold)
            self._find_faces = self._detect_with_yunet

    def apply(self, image: np.ndarray) -> np.ndarray:
        """Retorna uma cópia do frame com os rostos borrados; a entrada não é alterada."""
        output = image.copy()
        padded = [
            _pad_box(x, y, w, h, self._padding, image.shape[:2])
            for x, y, w, h in self._find_faces(image)
        ]
        detected = [box for box in padded if box[2] > 0 and box[3] > 0]
        for box in self._update_memory(detected):
            x, y, w, h = box
            output[y : y + h, x : x + w] = self._obscure(output[y : y + h, x : x + w])
        return output

    def _obscure(self, roi: np.ndarray) -> np.ndarray:
        if self._method == "fill":
            return np.zeros_like(roi)
        if self._method == "pixelate":
            return _pixelate(roi)
        if self._method == "mosaic":
            return _mosaic(roi)
        height, width = roi.shape[:2]
        kernel = _blur_kernel_for(width, height)
        return cv2.GaussianBlur(roi, (kernel, kernel), 0)

    def _detect_with_yunet(self, image: np.ndarray) -> list[Box]:
        height, width = image.shape[:2]
        self._yunet.setInputSize((width, height))
        _, faces = self._yunet.detect(image)
        if faces is None:
            return []
        return [tuple(int(v) for v in face[:4]) for face in faces]

    def _update_memory(self, current: list[Box]) -> list[Box]:
        kept = [
            (box, age + 1)
            for box, age in self._memory
            if age + 1 <= self._memory_frames and not any(_iou(box, c) > 0.3 for c in current)
        ]
        self._memory = [(box, 0) for box in current] + kept
        return [box for box, _ in self._memory]

    @staticmethod
    def _load_yunet(score_threshold: float) -> cv2.FaceDetectorYN:
        if not YUNET_MODEL_PATH.is_file():
            raise RuntimeError(f"Modelo YuNet não encontrado: {YUNET_MODEL_PATH}")
        return cv2.FaceDetectorYN.create(
            str(YUNET_MODEL_PATH), "", (320, 320), score_threshold, 0.3, 5000
        )


def _pad_box(x: int, y: int, w: int, h: int, padding: float, frame_shape: tuple[int, int]) -> Box:
    dx, dy = int(w * padding), int(h * padding)
    height, width = frame_shape
    x0, y0 = max(0, x - dx), max(0, y - dy)
    x1, y1 = min(width, x + w + dx), min(height, y + h + dy)
    return x0, y0, x1 - x0, y1 - y0


def _iou(a: Box, b: Box) -> float:
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    ix = max(0, min(ax0 + aw, bx0 + bw) - max(ax0, bx0))
    iy = max(0, min(ay0 + ah, by0 + bh) - max(ay0, by0))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def _pixelate(roi: np.ndarray) -> np.ndarray:
    """Reduz a região a uma grade de ~8 blocos no maior lado e amplia de volta."""
    height, width = roi.shape[:2]
    block = max(1, max(width, height) // 8)
    small = cv2.resize(roi, (max(1, width // block), max(1, height // block)), interpolation=cv2.INTER_AREA)
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_NEAREST)


def _mosaic(roi: np.ndarray) -> np.ndarray:
    """Grade de ~8 blocos, cada um preto ou branco conforme a luminância média.

    Mantém a estrutura grossa da região (o efeito quadriculado preto e branco)
    e descarta os detalhes, como a pixelização comum.
    """
    height, width = roi.shape[:2]
    block = max(1, max(width, height) // 8)
    cols, rows = max(1, width // block), max(1, height // block)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (cols, rows), interpolation=cv2.INTER_AREA)
    black_white = np.where(small < 128, 0, 255).astype(np.uint8)
    enlarged = cv2.resize(black_white, (width, height), interpolation=cv2.INTER_NEAREST)
    return cv2.cvtColor(enlarged, cv2.COLOR_GRAY2BGR)


def _blur_kernel_for(width: int, height: int) -> int:
    """Kernel ímpar proporcional ao tamanho do rosto, para anonimizar de forma efetiva."""
    kernel = max(15, (max(width, height) // 3) | 1)
    return kernel if kernel % 2 == 1 else kernel + 1
