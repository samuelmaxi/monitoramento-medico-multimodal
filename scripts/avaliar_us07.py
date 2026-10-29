"""Avalia pesos YOLOv8 contra um dataset de detecção anotado em data.yaml."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    raiz = Path(__file__).resolve().parent.parent
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))

    from video.metricas import avaliar_pesos, metricas_coco_baseline

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "data",
        nargs="?",
        type=Path,
        help="data.yaml do dataset YOLO anotado (obrigatório sem --baseline)",
    )
    parser.add_argument(
        "--weights", default="yolov8n.pt", help="pesos COCO ou checkpoint customizado"
    )
    parser.add_argument("--split", default="val", choices=("val", "test", "train"))
    parser.add_argument("--device", help="cpu, mps, cuda index etc.")
    parser.add_argument("--output", type=Path, help="salvar resultados JSON neste caminho")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help=(
            "imprime o baseline COCO do model card (yolov8n) sem baixar dataset "
            "nem usar GPU; não substitui medição em ground truth anotado"
        ),
    )
    args = parser.parse_args(argv)

    if not args.baseline and args.data is None:
        parser.error("data é obrigatório a menos que --baseline seja usado")

    if args.baseline:
        metricas = metricas_coco_baseline()
    else:
        metricas = avaliar_pesos(
            str(args.weights), args.data, split=args.split, dispositivo=args.device
        )
    if args.output:
        metricas.salvar_json(args.output)
    print(json.dumps(metricas.para_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
