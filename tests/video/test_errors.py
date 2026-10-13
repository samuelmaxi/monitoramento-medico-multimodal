import json
import logging

import pytest

from app.video.config import VideoConfig
from app.video.errors import CorruptedVideoError, InvalidVideoFormatError, VideoError
from app.video.pipeline import process_video


def _config(tmp_path, **overrides):
    return VideoConfig(output_dir=tmp_path / "saida", anonymize=False, **overrides)


def test_erros_de_video_herdam_da_base():
    assert issubclass(InvalidVideoFormatError, VideoError)
    assert issubclass(CorruptedVideoError, VideoError)


def test_arquivo_de_texto_renomeado_para_mp4_falha_sem_derrubar_pipeline(tmp_path, caplog):
    path = tmp_path / "falso.mp4"
    path.write_bytes(b"texto puro nao e video" * 50)

    with caplog.at_level(logging.ERROR, logger="app.video.pipeline"):
        result = process_video(path, _config(tmp_path))

    assert result["status"] == "failed"
    assert result["reason"]
    assert result["frames"] == []
    assert any("falso.mp4" in record.getMessage() for record in caplog.records)


def test_extensao_invalida_marca_falha_e_grava_json(tmp_path):
    path = tmp_path / "exame.txt"
    path.write_bytes(b"x")
    config = _config(tmp_path)

    result = process_video(path, config)

    assert result["status"] == "failed"
    assert "Extensão não suportada" in result["reason"]
    saved = json.loads((config.output_dir / "exame.json").read_text(encoding="utf-8"))
    assert saved["status"] == "failed"


def test_arquivo_inexistente_falha_sem_excecao(tmp_path):
    result = process_video(tmp_path / "nao_existe.mp4", _config(tmp_path))

    assert result["status"] == "failed"


def test_video_truncado_gera_status_parcial(make_video, tmp_path, monkeypatch):
    path = make_video(frames=60, fps=30.0)
    # Simula vídeo que anuncia 60 frames mas entrega só os primeiros 12.
    from app.video import reader as reader_module

    real_capture = reader_module.cv2.VideoCapture

    # Wrapper em Python (e não subclasse do tipo nativo) para não interferir no ciclo de vida do OpenCV.
    class _TruncatedCapture:
        def __init__(self, *args, **kwargs):
            self._cap = real_capture(*args, **kwargs)
            self._entregues = 0

        def read(self):
            if self._entregues >= 12:
                return False, None
            self._entregues += 1
            return self._cap.read()

        def __getattr__(self, name):
            return getattr(self._cap, name)

    monkeypatch.setattr(reader_module.cv2, "VideoCapture", _TruncatedCapture)

    result = process_video(path, _config(tmp_path))

    assert result["status"] == "partial"
    assert "12 de 60" in result["reason"]
    assert result["frames"]


def test_falha_de_anonimizacao_interrompe_sem_gravar_frames(make_video, tmp_path):
    """Se o anonimizador quebrar, nada é gravado: frames sem blur nunca vão ao disco."""

    class _Quebrado:
        def apply(self, image):
            raise RuntimeError("falha simulada no blur")

    path = make_video(frames=12)
    config = VideoConfig(output_dir=tmp_path / "saida", anonymize=True)

    with pytest.raises(RuntimeError):
        process_video(path, config, anonymizer=_Quebrado())

    assert not (config.output_dir / "sintetico.json").exists()
    assert not list(config.output_dir.glob("*_anotado.mp4"))
