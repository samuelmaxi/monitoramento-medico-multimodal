"""Helpers de avaliação reproduzível para detecção de objetos US07."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .detector import DetectorYolo

NOMES_METRICAS = ("mAP@0.5", "mAP@0.5:0.95", "precision", "recall")

REFERENCIA_YOLOV8N_COCO = "https://docs.ultralytics.com/models/yolov8/"
"""Model card oficial (Ultralytics) com as métricas de baseline do ``yolov8n.pt``."""


@dataclass(frozen=True, slots=True)
class MetricasDeteccao:
    """Métricas de validação produzidas pelo validator oficial Ultralytics."""

    mapa_50: float
    mapa_50_95: float
    precisao: float
    recall: float
    pesos: str
    versao_ultralytics: str
    conjunto: str
    classes: tuple[str, ...] = ()
    mapa_50_por_classe: tuple[tuple[str, float | None], ...] = ()

    def para_dict(self) -> dict[str, object]:
        return {
            "mAP@0.5": self.mapa_50,
            "mAP@0.5:0.95": self.mapa_50_95,
            "precision": self.precisao,
            "recall": self.recall,
            "pesos": self.pesos,
            "versao_ultralytics": self.versao_ultralytics,
            "conjunto": self.conjunto,
            "classes": list(self.classes),
            "mAP@0.5_por_classe": dict(self.mapa_50_por_classe),
        }

    def salvar_json(self, caminho: str | Path) -> Path:
        destino = Path(caminho)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(self.para_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return destino


@dataclass(frozen=True, slots=True)
class MetricasCocoBaseline:
    """Baseline COCO do checkpoint pré-treinado, conforme o model card oficial.

    Registra o desempenho esperado de ``yolov8n.pt`` no COCO val2017 como a
    *alternativa sem fine-tuning* prevista no DoD da US07. Apenas valores
    publicados na página oficial são preenchidos: o model card publica só
    ``mAP@0.5:0.95`` (**37,3**); ``mAP@0.5``/precision/recall ficam ``None``
    ("não publicado"), nunca estimados — medição própria exige ground truth
    anotado (item 5 do DoD).
    """

    mapa_50_95: float
    mapa_50: float | None = None
    precisao: float | None = None
    recall: float | None = None
    pesos: str = "yolov8n.pt"
    origem: str = "coco"
    referencia: str = REFERENCIA_YOLOV8N_COCO

    def para_dict(self) -> dict[str, float | str | None]:
        return {
            "mAP@0.5": self.mapa_50,
            "mAP@0.5:0.95": self.mapa_50_95,
            "precision": self.precisao,
            "recall": self.recall,
            "pesos": self.pesos,
            "origem": self.origem,
            "referencia": self.referencia,
        }

    def salvar_json(self, caminho: str | Path) -> Path:
        destino = Path(caminho)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(self.para_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return destino


REFERENCIA_COCO_MEDIDO = (
    "saida/video/metricas_coco_val2017.json — scripts/avaliar_us07.py "
    "<coco_val2017>/data.yaml --weights yolov8n.pt --split val (conf 0.001, iou 0.5)"
)
"""Medição própria no COCO val2017 (5000 imagens), mesmo validator do item 5 do DoD."""


def metricas_coco_baseline() -> MetricasCocoBaseline:
    """Baseline COCO do ``yolov8n.pt`` medido no COCO val2017 (Ultralytics 8.4.177).

    Os quatro valores vêm da mesma execução. O model card publica só
    ``mAP@0.5:0.95 = 0,373`` (``REFERENCIA_YOLOV8N_COCO``); a diferença para o
    medido (0,366) vem do NMS ``iou=0.5`` do projeto contra 0,7 do Ultralytics.
    """
    return MetricasCocoBaseline(
        mapa_50_95=0.3657,
        mapa_50=0.5283,
        precisao=0.6366,
        recall=0.4874,
        origem="coco_val2017_medido",
        referencia=REFERENCIA_COCO_MEDIDO,
    )


def avaliar_detector(
    detector: DetectorYolo,
    data_yaml: str | Path,
    *,
    split: str = "val",
) -> MetricasDeteccao:
    """Executa validação quantitativa no dataset anotado especificado.

    ``mAP@0.5`` é a AP a IoU 0.50; ``mAP@0.5:0.95`` agrega IoUs 0.50..0.95.
    Não há geração de métricas sobre vídeo sem ground truth.
    """
    conjunto = Path(data_yaml)
    if not conjunto.is_file():
        raise FileNotFoundError(f"data.yaml do conjunto anotado não encontrado: {conjunto}")
    resultado = detector.val_metricas(str(conjunto), split=split)
    nomes_classes = resultado.get("classes", ())
    mapas_por_classe = resultado.get("mAP@0.5_por_classe", {})
    return MetricasDeteccao(
        mapa_50=float(resultado["mAP@0.5"]),
        mapa_50_95=float(resultado["mAP@0.5:0.95"]),
        precisao=float(resultado["precision"]),
        recall=float(resultado["recall"]),
        pesos=detector.config.pesos,
        versao_ultralytics=detector.versao,
        conjunto=str(conjunto.resolve()),
        classes=tuple(str(nome) for nome in nomes_classes),
        mapa_50_por_classe=tuple(
            (str(nome), float(mapa) if mapa is not None else None)
            for nome, mapa in mapas_por_classe.items()
        ),
    )


def avaliar_pesos(
    pesos: str,
    data_yaml: str | Path,
    *,
    split: str = "val",
    dispositivo: str | None = None,
    classes: Sequence[str] | None = None,
) -> MetricasDeteccao:
    """Cria detector YOLO e executa validação num dataset rotulado."""
    from .detector import ConfiguracaoDetector

    config = (
        ConfiguracaoDetector(pesos=pesos, dispositivo=dispositivo, rastrear=False)
        if classes is None
        else ConfiguracaoDetector(
            pesos=pesos,
            classes=tuple(classes),
            dispositivo=dispositivo,
            rastrear=False,
        )
    )
    detector = DetectorYolo(config, rastrear=False)
    return avaliar_detector(detector, data_yaml, split=split)


__all__ = [
    "NOMES_METRICAS",
    "REFERENCIA_YOLOV8N_COCO",
    "MetricasCocoBaseline",
    "MetricasDeteccao",
    "avaliar_detector",
    "avaliar_pesos",
    "metricas_coco_baseline",
]
