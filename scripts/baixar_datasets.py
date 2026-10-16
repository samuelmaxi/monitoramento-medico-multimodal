"""Baixa datasets de instrumentos cirúrgicos e monitores em formato YOLO.

Uso:
    uv run --with datasets --with roboflow python scripts/baixar_datasets.py [--max-imagens N]
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from datasets import load_dataset
from dotenv import load_dotenv
from roboflow import Roboflow

REPO = "joonhaim/surgical-tool-recognition-full-multiview"
ROOT = Path("downloads/instrumentos_hf")
CLASSES = ["clamp", "needle_holder", "scalpel", "shear", "tweezer"]
SPLITS = {"train": "train", "validation": "val", "test": "test"}
MONITORS_ROOT = Path("downloads/monitores_us07")


def baixar_monitores() -> None:
    load_dotenv()
    chave = os.environ.get("ROBOFLOW_API_KEY")
    if not chave:
        raise SystemExit("ROBOFLOW_API_KEY ausente no .env")
    if MONITORS_ROOT.exists() and any(MONITORS_ROOT.iterdir()):
        raise SystemExit(f"Destino já existe e não está vazio: {MONITORS_ROOT}")

    rf = Roboflow(api_key=chave)
    rf.workspace("anndev").project("hospitalequipmentdetection").version(7).download(
        "yolov8", location=str(MONITORS_ROOT)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa os datasets de instrumentos e monitores.")
    parser.add_argument(
        "--max-imagens",
        type=int,
        help="Máximo de imagens do Hugging Face por split (train, val e test).",
    )
    args = parser.parse_args()
    if args.max_imagens is not None and args.max_imagens < 1:
        parser.error("--max-imagens deve ser maior que zero")

    baixar_instrumentos(args.max_imagens)
    print("Baixando dataset de monitores...")
    baixar_monitores()
    print(f"Dataset de monitores baixado: {MONITORS_ROOT}")


def baixar_instrumentos(max_imagens: int | None) -> None:
    if ROOT.exists() and any(ROOT.iterdir()):
        raise SystemExit(f"Destino já existe e não está vazio: {ROOT}")

    print("Baixando dataset de instrumentos do Hugging Face...", flush=True)
    for fonte, destino in SPLITS.items():
        imagens = ROOT / "images" / destino
        labels = ROOT / "labels" / destino
        imagens.mkdir(parents=True, exist_ok=True)
        labels.mkdir(parents=True, exist_ok=True)
        vistos: set[str] = set()
        quantidade = 0

        print(f"Iniciando split {destino}...", flush=True)
        dataset = load_dataset(REPO, split=fonte, streaming=True)
        for registro in dataset:
            if max_imagens is not None and quantidade >= max_imagens:
                break
            imagem = registro["image"]
            stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", registro["stem"])
            if stem in vistos:
                raise ValueError(f"Nome de imagem duplicado no split {destino}: {stem}")
            vistos.add(stem)

            largura, altura = imagem.size
            imagem.convert("RGB").save(imagens / f"{stem}.jpg", format="JPEG")

            objetos = registro["objects"]
            caixas = objetos["bbox"]
            nomes = objetos["category_names"]
            if len(caixas) != len(nomes):
                raise ValueError(f"Caixas e classes divergentes: {stem}")

            linhas = []
            for caixa, nome in zip(caixas, nomes, strict=True):
                if nome not in CLASSES:
                    raise ValueError(f"Classe inesperada {nome!r} na imagem {stem}")

                x, y, caixa_largura, caixa_altura = map(float, caixa)
                x1 = max(0.0, x)
                y1 = max(0.0, y)
                x2 = min(float(largura), x + caixa_largura)
                y2 = min(float(altura), y + caixa_altura)
                if x2 <= x1 or y2 <= y1:
                    raise ValueError(f"Caixa inválida na imagem {stem}: {caixa}")

                centro_x = ((x1 + x2) / 2) / largura
                centro_y = ((y1 + y2) / 2) / altura
                largura_norm = (x2 - x1) / largura
                altura_norm = (y2 - y1) / altura
                linhas.append(
                    f"{CLASSES.index(nome)} {centro_x:.6f} {centro_y:.6f} "
                    f"{largura_norm:.6f} {altura_norm:.6f}"
                )

            (labels / f"{stem}.txt").write_text(
                "\n".join(linhas) + ("\n" if linhas else ""),
                encoding="utf-8",
            )
            quantidade += 1
            if quantidade % 100 == 0:
                print(f"{destino}: {quantidade} imagens processadas", flush=True)

        print(f"{destino}: {quantidade} imagens concluídas", flush=True)

    data_yaml = [
        f"path: {json.dumps(str(ROOT.resolve()))}",
        "train: images/train",
        "val: images/val",
        "test: images/test",
        "names:",
        *[f"  {indice}: {nome}" for indice, nome in enumerate(CLASSES)],
    ]
    (ROOT / "data.yaml").write_text("\n".join(data_yaml) + "\n", encoding="utf-8")
    print(f"YOLO dataset criado: {ROOT}")


if __name__ == "__main__":
    main()
