"""Testes de leitura de vídeo, amostragem e configuração (sem os pesos do OpenPose)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from video.pose.config import ConfigError, config_from_dict, load_config
from video.pose.extract import VideoReadError, iter_sampled_frames, video_id_for

CONFIG_PATH = Path(__file__).resolve().parents[1] / "pose" / "config.yaml"


def _write_video(path: Path, fps: float, n_frames: int) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (64, 48))
    assert writer.isOpened()
    for i in range(n_frames):
        writer.write(np.full((48, 64, 3), i % 256, np.uint8))
    writer.release()
    return path


class TestAmostragem:
    def test_30fps_para_10fps(self, tmp_path):
        video = _write_video(tmp_path / "v.avi", 30, 30)
        frames = list(iter_sampled_frames(video, target_fps=10))
        assert [f[0] for f in frames] == list(range(0, 30, 3))
        assert [f[1] for f in frames] == list(range(0, 1000, 100))
        assert frames[0][2].shape == (48, 64, 3)

    def test_fps_nao_inteiro(self, tmp_path):
        video = _write_video(tmp_path / "v.avi", 25, 25)
        idx = [f[0] for f in iter_sampled_frames(video, target_fps=10)]
        assert idx == [0, 3, 5, 8, 10, 13, 15, 18, 20, 23]

    def test_alvo_acima_do_fps_usa_todos(self, tmp_path):
        video = _write_video(tmp_path / "v.avi", 5, 6)
        assert [f[0] for f in iter_sampled_frames(video, target_fps=10)] == list(range(6))

    def test_max_frames(self, tmp_path):
        video = _write_video(tmp_path / "v.avi", 30, 30)
        assert len(list(iter_sampled_frames(video, target_fps=10, max_frames=4))) == 4

    def test_video_corrompido(self, tmp_path):
        bad = tmp_path / "ruim.mp4"
        bad.write_bytes(b"isto nao e um video" * 100)
        with pytest.raises(VideoReadError):
            list(iter_sampled_frames(bad, target_fps=10))

    def test_video_inexistente(self, tmp_path):
        with pytest.raises(VideoReadError):
            list(iter_sampled_frames(tmp_path / "nao_existe.mp4", target_fps=10))


class TestVideoId:
    def test_relativo_a_raiz(self, tmp_path):
        path = tmp_path / "Fall" / "Bed" / "Raw Video" / "f_raw_b_3" / "B_N_01.mp4"
        path.parent.mkdir(parents=True)
        path.touch()
        assert video_id_for(path, tmp_path) == "Fall__Bed__Raw_Video__f_raw_b_3__B_N_01"

    def test_sem_raiz_usa_nome(self):
        assert video_id_for("x/y/PM_000-Camera17-30fps.mp4") == "PM_000-Camera17-30fps"


class TestConfig:
    def test_config_do_repositorio_carrega(self):
        cfg = load_config(CONFIG_PATH)
        assert cfg.model.name in {"BODY_25", "COCO"}
        assert cfg.sampling.target_fps == 10
        assert cfg.keypoints.conf_threshold == 0.3
        assert cfg.keypoints.min_valid_keypoints == 8

    def test_chave_ausente(self):
        data = yaml.safe_load(CONFIG_PATH.read_text())
        del data["keypoints"]["conf_threshold"]
        with pytest.raises(ConfigError, match="keypoints.conf_threshold"):
            config_from_dict(data)

    def test_backend_invalido(self):
        data = yaml.safe_load(CONFIG_PATH.read_text())
        data["model"]["backend"] = "tpu"
        with pytest.raises(ConfigError, match="backend"):
            config_from_dict(data)
