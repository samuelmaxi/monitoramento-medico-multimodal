import cv2
import numpy as np
import pytest


@pytest.fixture
def make_video(tmp_path):
    """Gera um vídeo sintético pequeno em tmp_path (sem depender de arquivos externos)."""

    def _make(
        name: str = "sintetico.mp4",
        frames: int = 30,
        fps: float = 30.0,
        size: tuple[int, int] = (64, 48),  # largura, altura
        codec: str = "mp4v",
    ):
        path = tmp_path / name
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*codec), fps, size)
        assert writer.isOpened(), f"OpenCV não conseguiu gravar {name} no codec {codec}"
        width, height = size
        for i in range(frames):
            writer.write(np.full((height, width, 3), i % 255, dtype=np.uint8))
        writer.release()
        return path

    return _make
