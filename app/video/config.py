import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_TARGET_FPS = 5.0
DEFAULT_OUTPUT_DIR = Path("saida/video")
SUPPORTED_EXTENSIONS = (".mp4", ".avi")
# fill: tarja opaca (mais forte, sem informação da região); pixelate: grade grossa;
# mosaic: grade grossa em preto e branco (xadrez); blur: gaussiano.
ANONYMIZE_METHODS = ("fill", "pixelate", "mosaic", "blur")
DEFAULT_ANONYMIZE_METHOD = "fill"


@dataclass(frozen=True)
class VideoConfig:
    target_fps: float = DEFAULT_TARGET_FPS
    anonymize: bool = True
    anonymize_method: str = DEFAULT_ANONYMIZE_METHOD
    save_annotated: bool = False
    save_anonymized: bool = False
    output_dir: Path = DEFAULT_OUTPUT_DIR
    supported_extensions: tuple[str, ...] = field(default=SUPPORTED_EXTENSIONS)

    def __post_init__(self) -> None:
        if self.target_fps <= 0:
            raise ValueError("target_fps deve ser maior que zero.")
        if self.anonymize_method not in ANONYMIZE_METHODS:
            raise ValueError(
                f"anonymize_method inválido '{self.anonymize_method}'. "
                f"Use um de: {', '.join(ANONYMIZE_METHODS)}."
            )

    @classmethod
    def from_env(cls) -> "VideoConfig":
        """Lê as variáveis VIDEO_* do ambiente, carregando o .env se existir."""
        load_dotenv()
        return cls(
            target_fps=float(os.getenv("VIDEO_TARGET_FPS", DEFAULT_TARGET_FPS)),
            anonymize_method=os.getenv("VIDEO_ANONYMIZE_METHOD", DEFAULT_ANONYMIZE_METHOD),
            output_dir=Path(os.getenv("VIDEO_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))),
        )
