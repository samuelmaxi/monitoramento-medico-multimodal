"""Métricas apenas vêm de avaliação Ultralytics em dataset anotado real."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from video import ConfiguracaoDetector, DetectorYolo, avaliar_detector
from video.metricas import MetricasCocoBaseline, MetricasDeteccao, metricas_coco_baseline


class ModeloValidadorFalso:
    def __init__(self):
        self.names = {0: "person"}

    def val(self, *, data, verbose, **argumentos):
        self.chamada = {"data": data, "verbose": verbose, **argumentos}
        return SimpleNamespace(box=SimpleNamespace(map50=0.71, map=0.42, mp=0.8, mr=0.6))


def test_metricas_lidas_da_validacao_ultralytics_e_salvas(tmp_path: Path):
    yaml = tmp_path / "data.yaml"
    yaml.write_text("path: .\ntrain: images/train\nval: images/val\nnames: {0: person}\n")
    modelo = ModeloValidadorFalso()
    detector = DetectorYolo(
        ConfiguracaoDetector(classes=("person",), rastrear=False),
        rastrear=False,
        modelo=modelo,
    )
    metricas = avaliar_detector(detector, yaml)
    assert metricas == MetricasDeteccao(
        mapa_50=0.71,
        mapa_50_95=0.42,
        precisao=0.8,
        recall=0.6,
        pesos="yolov8n.pt",
        versao_ultralytics=detector.versao,
        conjunto=str(yaml.resolve()),
    )
    assert modelo.chamada["split"] == "val"
    assert modelo.chamada["conf"] == 0.001
    assert modelo.chamada["iou"] == 0.5
    destino = metricas.salvar_json(tmp_path / "report" / "metrics.json")
    assert '"mAP@0.5": 0.71' in destino.read_text(encoding="utf-8")


def test_dataset_ausente_nao_finge_metricas(tmp_path: Path):
    detector = DetectorYolo(
        ConfiguracaoDetector(classes=("person",), rastrear=False),
        rastrear=False,
        modelo=ModeloValidadorFalso(),
    )
    with pytest.raises(FileNotFoundError, match="conjunto anotado"):
        avaliar_detector(detector, tmp_path / "ausente.yaml")


def test_baseline_coco_usa_numero_oficial_publicado_no_model_card():
    m = metricas_coco_baseline()
    assert m == MetricasCocoBaseline(mapa_50_95=0.373)
    assert m.mapa_50 is None
    assert m.precisao is None
    assert m.recall is None
    assert m.pesos == "yolov8n.pt"
    assert m.origem == "coco"
    assert m.referencia.startswith("https://docs.ultralytics.com/models/yolov8")


def test_baseline_coco_salvar_json(tmp_path: Path):
    destino = metricas_coco_baseline().salvar_json(tmp_path / "baseline.json")
    texto = destino.read_text(encoding="utf-8")
    assert '"mAP@0.5:0.95": 0.373' in texto
    assert '"origem": "coco"' in texto
    assert '"referencia"' in texto
