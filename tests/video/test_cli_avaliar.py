"""Testes do CLI de avaliação US07 (``scripts/avaliar_us07.py``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.avaliar_us07 import main


def test_cli_baseline_imprime_metricas_coco_sem_dataset(capsys):
    assert main(["--baseline"]) == 0
    dados = json.loads(capsys.readouterr().out)
    assert dados["mAP@0.5:0.95"] == 0.373
    assert dados["origem"] == "coco"
    assert dados["pesos"] == "yolov8n.pt"
    assert dados["mAP@0.5"] is None


def test_cli_baseline_salva_em_output(tmp_path: Path):
    saida = tmp_path / "metricas.json"
    assert main(["--baseline", "--output", str(saida)]) == 0
    dados = json.loads(saida.read_text(encoding="utf-8"))
    assert dados["referencia"].startswith("https://docs.ultralytics.com/models/yolov8")


def test_cli_sem_baseline_exige_data():
    with pytest.raises(SystemExit) as erro:
        main([])
    assert erro.value.code == 2
