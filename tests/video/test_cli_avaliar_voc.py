"""Testes do CLI de avaliação IoU>=0,5 (``scripts/avaliar_voc_us07.py``)."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from scripts.avaliar_voc_us07 import main


def _dataset(tmp_path: Path, *, label: str) -> tuple[Path, Path]:
    imagens = tmp_path / "images"
    labels = tmp_path / "labels"
    imagens.mkdir()
    labels.mkdir()
    cv2.imwrite(str(imagens / "a.jpg"), np.zeros((50, 100, 3), dtype=np.uint8))
    (labels / "a.txt").write_text(label, encoding="utf-8")
    return imagens, labels


def _predicoes(tmp_path: Path, linhas: list[dict]) -> Path:
    caminho = tmp_path / "pred.jsonl"
    caminho.write_text("\n".join(json.dumps(x) for x in linhas), encoding="utf-8")
    return caminho


def test_cli_aprova_quando_iou_acima_do_limiar(tmp_path: Path, capsys):
    imagens, labels = _dataset(tmp_path, label="0 0.5 0.5 0.4 0.2\n")
    pred = _predicoes(
        tmp_path,
        [{"imagem": "a.jpg", "class_id": 0, "confianca": 0.9, "x1": 30, "y1": 20, "x2": 70, "y2": 30}],
    )
    assert main(["--imagens", str(imagens), "--labels", str(labels), "--predicoes", str(pred)]) == 0
    dados = json.loads(capsys.readouterr().out)
    assert dados["avaliacao"]["mAP@0.5"] == 1.0
    assert dados["gate"]["aprovado"] is True


def test_cli_reprova_sem_ground_truth(tmp_path: Path, capsys):
    imagens, labels = _dataset(tmp_path, label="")
    pred = _predicoes(
        tmp_path,
        [{"imagem": "a.jpg", "class_id": 0, "confianca": 0.9, "x1": 0, "y1": 0, "x2": 10, "y2": 10}],
    )
    assert main(["--imagens", str(imagens), "--labels", str(labels), "--predicoes", str(pred)]) == 1
    dados = json.loads(capsys.readouterr().out)
    assert dados["gate"]["medido"] is False
    assert dados["gate"]["aprovado"] is False


def test_cli_salva_output(tmp_path: Path, capsys):
    imagens, labels = _dataset(tmp_path, label="0 0.5 0.5 0.4 0.2\n")
    pred = _predicoes(
        tmp_path,
        [{"imagem": "a", "class_id": 0, "confianca": 0.9, "caixa": [30, 20, 70, 30]}],
    )
    saida = tmp_path / "reports" / "voc.json"
    codigo = main(
        [
            "--imagens",
            str(imagens),
            "--labels",
            str(labels),
            "--predicoes",
            str(pred),
            "--output",
            str(saida),
        ]
    )
    capsys.readouterr()
    assert codigo == 0
    assert json.loads(saida.read_text(encoding="utf-8"))["avaliacao"]["mAP@0.5"] == 1.0


def test_cli_exige_origem_de_predicoes(tmp_path: Path):
    imagens, labels = _dataset(tmp_path, label="0 0.5 0.5 0.4 0.2\n")
    with pytest.raises(SystemExit) as erro:
        main(["--imagens", str(imagens), "--labels", str(labels)])
    assert erro.value.code == 2


def test_cli_predicoes_e_pesos_exclusivos(tmp_path: Path):
    imagens, labels = _dataset(tmp_path, label="0 0.5 0.5 0.4 0.2\n")
    with pytest.raises(SystemExit) as erro:
        main(
            [
                "--imagens",
                str(imagens),
                "--labels",
                str(labels),
                "--predicoes",
                str(tmp_path / "p.jsonl"),
                "--pesos",
                "yolov8n.pt",
            ]
        )
    assert erro.value.code == 2


def test_cli_predicoes_ausentes(tmp_path: Path, capsys):
    imagens, labels = _dataset(tmp_path, label="0 0.5 0.5 0.4 0.2\n")
    codigo = main(
        [
            "--imagens",
            str(imagens),
            "--labels",
            str(labels),
            "--predicoes",
            str(tmp_path / "nao_existe.jsonl"),
        ]
    )
    assert codigo == 1
    assert "erro:" in capsys.readouterr().err
