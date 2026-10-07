"""Definição dos modelos OpenPose suportados: nomes dos keypoints e pares do PAF.

Os pares e os índices dos canais de PAF são os de
``openpose/src/openpose/pose/poseParameters.cpp`` (POSE_BODY_PART_PAIRS e
POSE_MAP_INDEX). A saída da rede concatena primeiro os mapas de calor
(keypoints + fundo) e depois os PAFs, dois canais (x, y) por par.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    name: str
    keypoints: tuple[str, ...]
    # Pares (keypoint A, keypoint B) ligados por um PAF, na ordem do OpenPose.
    pairs: tuple[tuple[int, int], ...]
    # Para cada par, os canais (x, y) do PAF, contados a partir do primeiro PAF.
    paf_channels: tuple[tuple[int, int], ...]

    @property
    def n_heatmaps(self) -> int:
        # Keypoints + canal de fundo.
        return len(self.keypoints) + 1

    @property
    def n_channels(self) -> int:
        return self.n_heatmaps + 2 * len(self.pairs)


def _by_two(flat: tuple[int, ...]) -> tuple[tuple[int, int], ...]:
    return tuple((flat[i], flat[i + 1]) for i in range(0, len(flat), 2))


BODY_25 = ModelSpec(
    name="BODY_25",
    keypoints=(
        "Nose", "Neck", "RShoulder", "RElbow", "RWrist", "LShoulder", "LElbow",
        "LWrist", "MidHip", "RHip", "RKnee", "RAnkle", "LHip", "LKnee", "LAnkle",
        "REye", "LEye", "REar", "LEar", "LBigToe", "LSmallToe", "LHeel",
        "RBigToe", "RSmallToe", "RHeel",
    ),
    pairs=_by_two((
        1, 8, 1, 2, 1, 5, 2, 3, 3, 4, 5, 6, 6, 7, 8, 9, 9, 10, 10, 11, 8, 12,
        12, 13, 13, 14, 1, 0, 0, 15, 15, 17, 0, 16, 16, 18, 2, 17, 5, 18,
        14, 19, 19, 20, 14, 21, 11, 22, 22, 23, 11, 24,
    )),
    paf_channels=_by_two((
        0, 1, 14, 15, 22, 23, 16, 17, 18, 19, 24, 25, 26, 27, 6, 7, 2, 3, 4, 5,
        8, 9, 10, 11, 12, 13, 30, 31, 32, 33, 36, 37, 34, 35, 38, 39, 20, 21,
        28, 29, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51,
    )),
)

COCO = ModelSpec(
    name="COCO",
    keypoints=(
        "Nose", "Neck", "RShoulder", "RElbow", "RWrist", "LShoulder", "LElbow",
        "LWrist", "RHip", "RKnee", "RAnkle", "LHip", "LKnee", "LAnkle", "REye",
        "LEye", "REar", "LEar",
    ),
    pairs=_by_two((
        1, 2, 1, 5, 2, 3, 3, 4, 5, 6, 6, 7, 1, 8, 8, 9, 9, 10, 1, 11, 11, 12,
        12, 13, 1, 0, 0, 14, 14, 16, 0, 15, 15, 17, 2, 16, 5, 17,
    )),
    paf_channels=_by_two((
        12, 13, 20, 21, 14, 15, 16, 17, 22, 23, 24, 25, 0, 1, 2, 3, 4, 5, 6, 7,
        8, 9, 10, 11, 28, 29, 30, 31, 34, 35, 32, 33, 36, 37, 18, 19, 26, 27,
    )),
)

MODELS: dict[str, ModelSpec] = {m.name: m for m in (BODY_25, COCO)}


def get_model_spec(name: str) -> ModelSpec:
    try:
        return MODELS[name.upper()]
    except KeyError:
        raise ValueError(f"modelo OpenPose desconhecido: {name!r} (opções: {', '.join(MODELS)})") from None
