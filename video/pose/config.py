"""Leitura do ``config.yaml`` da extração de pose.

Todos os parâmetros vêm do arquivo; o código não tem valores padrão escondidos.
Uma chave ausente gera :class:`ConfigError` com o caminho da chave, para o erro
aparecer na carga e não no meio de um lote.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Union

import yaml


class ConfigError(ValueError):
    """Configuração ausente ou inválida."""


@dataclass(frozen=True)
class ModelConfig:
    name: str
    prototxt: Path
    weights: Path
    input_height: int
    backend: str
    cuda_fp16: bool


@dataclass(frozen=True)
class SamplingConfig:
    target_fps: float
    max_frames: Optional[int]


@dataclass(frozen=True)
class KeypointConfig:
    conf_threshold: float
    min_valid_keypoints: int


@dataclass(frozen=True)
class PostprocessConfig:
    peak_threshold: float
    paf_samples: int
    paf_score_threshold: float
    paf_min_inlier_ratio: float


@dataclass(frozen=True)
class InputConfig:
    extensions: tuple[str, ...]
    exclude_substrings: tuple[str, ...]


@dataclass(frozen=True)
class OutputConfig:
    dir: Path
    summary_file: Path
    figures_dir: Path


@dataclass(frozen=True)
class Rehab24Config:
    videos_dir: Path
    segmentation_csv: Path
    joints_names: Path
    csv_separator: str


@dataclass(frozen=True)
class FallVisionConfig:
    root: Path


@dataclass(frozen=True)
class VisualizationConfig:
    frames_per_example: int
    tile_width: int
    point_radius_ratio: float
    line_thickness_ratio: float


@dataclass(frozen=True)
class PoseConfig:
    model: ModelConfig
    sampling: SamplingConfig
    keypoints: KeypointConfig
    postprocess: PostprocessConfig
    input: InputConfig
    output: OutputConfig
    rehab24: Rehab24Config
    fallvision: FallVisionConfig
    visualization: VisualizationConfig
    source: Optional[Path] = None


def _get(data: Mapping[str, Any], path: str) -> Any:
    atual: Any = data
    for parte in path.split("."):
        if not isinstance(atual, Mapping) or parte not in atual:
            raise ConfigError(f"chave obrigatória ausente no config: '{path}'")
        atual = atual[parte]
    return atual


def _positivo(valor: Any, path: str) -> float:
    if not isinstance(valor, (int, float)) or valor <= 0:
        raise ConfigError(f"'{path}' deve ser um número positivo (recebido: {valor!r})")
    return valor


def config_from_dict(data: Mapping[str, Any], source: Optional[Path] = None) -> PoseConfig:
    g = lambda path: _get(data, path)  # noqa: E731

    backend = str(g("model.backend")).lower()
    if backend not in {"auto", "cuda", "cpu"}:
        raise ConfigError(f"'model.backend' deve ser auto, cuda ou cpu (recebido: {backend!r})")
    input_height = int(_positivo(g("model.input_height"), "model.input_height"))
    if input_height % 16:
        raise ConfigError(f"'model.input_height' deve ser múltiplo de 16 (recebido: {input_height})")
    conf = float(g("keypoints.conf_threshold"))
    if not 0.0 <= conf <= 1.0:
        raise ConfigError(f"'keypoints.conf_threshold' deve estar em [0, 1] (recebido: {conf})")
    max_frames = g("sampling.max_frames")

    return PoseConfig(
        model=ModelConfig(
            name=str(g("model.name")).upper(),
            prototxt=Path(g("model.prototxt")),
            weights=Path(g("model.weights")),
            input_height=input_height,
            backend=backend,
            cuda_fp16=bool(g("model.cuda_fp16")),
        ),
        sampling=SamplingConfig(
            target_fps=float(_positivo(g("sampling.target_fps"), "sampling.target_fps")),
            max_frames=None if max_frames is None else int(_positivo(max_frames, "sampling.max_frames")),
        ),
        keypoints=KeypointConfig(
            conf_threshold=conf,
            min_valid_keypoints=int(g("keypoints.min_valid_keypoints")),
        ),
        postprocess=PostprocessConfig(
            peak_threshold=float(g("postprocess.peak_threshold")),
            paf_samples=int(_positivo(g("postprocess.paf_samples"), "postprocess.paf_samples")),
            paf_score_threshold=float(g("postprocess.paf_score_threshold")),
            paf_min_inlier_ratio=float(g("postprocess.paf_min_inlier_ratio")),
        ),
        input=InputConfig(
            extensions=tuple(str(e).lower() for e in g("input.extensions")),
            exclude_substrings=tuple(str(s).lower() for s in g("input.exclude_substrings")),
        ),
        output=OutputConfig(
            dir=Path(g("output.dir")),
            summary_file=Path(g("output.summary_file")),
            figures_dir=Path(g("output.figures_dir")),
        ),
        rehab24=Rehab24Config(
            videos_dir=Path(g("datasets.rehab24.videos_dir")),
            segmentation_csv=Path(g("datasets.rehab24.segmentation_csv")),
            joints_names=Path(g("datasets.rehab24.joints_names")),
            csv_separator=str(g("datasets.rehab24.csv_separator")),
        ),
        fallvision=FallVisionConfig(root=Path(g("datasets.fallvision.root"))),
        visualization=VisualizationConfig(
            frames_per_example=int(_positivo(g("visualization.frames_per_example"), "visualization.frames_per_example")),
            tile_width=int(_positivo(g("visualization.tile_width"), "visualization.tile_width")),
            point_radius_ratio=float(g("visualization.point_radius_ratio")),
            line_thickness_ratio=float(g("visualization.line_thickness_ratio")),
        ),
        source=source,
    )


def load_config(path: Union[str, Path]) -> PoseConfig:
    path = Path(path)
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, Mapping):
        raise ConfigError(f"{path}: o arquivo deve conter um mapeamento YAML")
    return config_from_dict(data, source=path)
