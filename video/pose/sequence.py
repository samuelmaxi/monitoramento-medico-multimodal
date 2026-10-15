"""Estrutura de dados da pose extraída de um vídeo e sua serialização em JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

# [x, y, conf] em pixels do quadro original, ou None se ausente/abaixo do limiar.
Keypoint = Optional[list[float]]

SCHEMA_VERSION = "1.0"


@dataclass
class PoseFrame:
    frame_idx: int
    timestamp_ms: int
    keypoints: dict[str, Keypoint]

    @property
    def n_valid(self) -> int:
        return sum(kp is not None for kp in self.keypoints.values())


@dataclass
class PoseSequence:
    video_id: str
    source_path: str
    keypoint_names: list[str]
    metadata: dict[str, Any]
    frames: list[PoseFrame] = field(default_factory=list)

    def skeleton_detected(self, frame: PoseFrame) -> bool:
        return frame.n_valid >= self.metadata["min_valid_keypoints"]

    def detection_rate(self) -> float:
        """Fração de quadros processados com esqueleto detectado (0 a 1)."""
        if not self.frames:
            return 0.0
        return sum(self.skeleton_detected(f) for f in self.frames) / len(self.frames)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "video_id": self.video_id,
            "source_path": self.source_path,
            "metadata": self.metadata,
            "keypoint_names": self.keypoint_names,
            "frames": [
                {
                    "frame_idx": f.frame_idx,
                    "timestamp_ms": f.timestamp_ms,
                    "n_valid_keypoints": f.n_valid,
                    "skeleton_detected": self.skeleton_detected(f),
                    "keypoints": f.keypoints,
                }
                for f in self.frames
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PoseSequence":
        return cls(
            video_id=data["video_id"],
            source_path=data["source_path"],
            keypoint_names=list(data["keypoint_names"]),
            metadata=dict(data["metadata"]),
            frames=[
                PoseFrame(f["frame_idx"], f["timestamp_ms"], dict(f["keypoints"]))
                for f in data["frames"]
            ],
        )

    def save_json(self, path: Union[str, Path]) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, separators=(",", ":"))
        tmp.replace(path)
        return path

    @classmethod
    def load_json(cls, path: Union[str, Path]) -> "PoseSequence":
        with Path(path).open(encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))
