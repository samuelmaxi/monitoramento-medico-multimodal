import json

import pytest

from app.video.config import VideoConfig
from app.video.detector import Detection
from app.video.pipeline import process_video


class _RecordingAnonymizer:
    def __init__(self):
        self.calls = []

    def apply(self, image):
        self.calls.append("anonymize")
        return image.copy()


class _RecordingDetector:
    def __init__(self, anonymizer):
        self._anonymizer = anonymizer

    def detect(self, image):
        self._anonymizer.calls.append("detect")
        return [Detection(label="person", confidence=0.9, bbox=(1, 2, 3, 4))]


def test_pipeline_grava_json_com_amostragem_e_timestamps(make_video, tmp_path):
    path = make_video(frames=30, fps=30.0)
    config = VideoConfig(target_fps=5.0, anonymize=False, output_dir=tmp_path / "saida")

    result = process_video(path, config)

    assert result["status"] == "ok"
    assert result["sampling"]["step"] == 6
    assert result["sampling"]["effective_fps"] == 5.0
    assert [f["frame_index"] for f in result["frames"]] == [0, 6, 12, 18, 24]
    assert [f["timestamp_ms"] for f in result["frames"]] == [0, 200, 400, 600, 800]

    saved = json.loads((config.output_dir / "sintetico.json").read_text(encoding="utf-8"))
    assert saved["frames"] == result["frames"]
    assert saved["sampling"]["target_fps"] == 5.0


def test_anonimizacao_ocorre_antes_da_deteccao(make_video, tmp_path):
    path = make_video(frames=12)
    anonymizer = _RecordingAnonymizer()
    config = VideoConfig(anonymize=True, output_dir=tmp_path / "saida")

    result = process_video(
        path,
        config,
        detector=_RecordingDetector(anonymizer),
        anonymizer=anonymizer,
    )

    assert result["anonymized"] is True
    assert anonymizer.calls[:2] == ["anonymize", "detect"]


def test_deteccoes_sao_serializadas_no_frame(make_video, tmp_path):
    path = make_video(frames=6)
    anonymizer = _RecordingAnonymizer()
    config = VideoConfig(anonymize=False, output_dir=tmp_path / "saida")

    result = process_video(path, config, detector=_RecordingDetector(anonymizer))

    assert result["frames"][0]["detections"] == [
        {"label": "person", "confidence": 0.9, "bbox": [1, 2, 3, 4]}
    ]


def test_video_anotado_é_gravado_quando_solicitado(make_video, tmp_path):
    path = make_video(frames=12)
    config = VideoConfig(anonymize=False, save_annotated=True, output_dir=tmp_path / "saida")

    result = process_video(path, config)

    assert result["output_video"] is not None
    assert (config.output_dir / "sintetico_anotado.mp4").is_file()


def test_video_anotado_nao_é_gravado_por_padrao(make_video, tmp_path):
    path = make_video(frames=12)
    config = VideoConfig(anonymize=False, output_dir=tmp_path / "saida")

    result = process_video(path, config)

    assert result["output_video"] is None
    assert not (config.output_dir / "sintetico_anotado.mp4").exists()


def test_fps_alvo_invalido_nao_é_aceito():
    with pytest.raises(ValueError):
        VideoConfig(target_fps=0)


def test_video_anonimizado_limpo_é_gravado_sem_anotacoes(make_video, tmp_path):
    path = make_video(frames=12)
    config = VideoConfig(anonymize=False, save_anonymized=True, output_dir=tmp_path / "saida")

    result = process_video(path, config, detector=_RecordingDetector(_RecordingAnonymizer()))

    assert result["output_anonymized_video"] is not None
    assert (config.output_dir / "sintetico_anonimizado.mp4").is_file()
    assert result["output_video"] is None


def test_video_limpo_nao_é_gravado_por_padrao(make_video, tmp_path):
    path = make_video(frames=12)
    config = VideoConfig(anonymize=False, output_dir=tmp_path / "saida")

    result = process_video(path, config)

    assert result["output_anonymized_video"] is None


def test_metodo_de_anonimizacao_fica_registrado_no_json(make_video, tmp_path):
    path = make_video(frames=6)
    config = VideoConfig(anonymize=True, anonymize_method="pixelate", output_dir=tmp_path / "saida")

    result = process_video(path, config, anonymizer=_RecordingAnonymizer())

    assert result["anonymize_method"] == "pixelate"


def test_config_rejeita_metodo_invalido():
    with pytest.raises(ValueError):
        VideoConfig(anonymize_method="inexistente")
