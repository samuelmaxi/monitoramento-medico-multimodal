"""Avalia detecções pelo critério IoU >= 0,50 (PASCAL VOC / mAP@0.5).

Compara predições com o ground truth em formato YOLO (``imagens/`` + ``labels/``),
casa as caixas por IoU >= ``--iou`` (mesma classe) e reporta TP/FP/FN, precisão,
revocação e ``mAP@0.5``. As predições vêm de um JSONL (``--predicoes``) ou são
geradas na hora por um modelo (``--pesos``). O gate do projeto
(``mAP@0.5 >= --minimo``) reprova quando não há ground truth (não medido).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    raiz = Path(__file__).resolve().parent.parent
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))

    from video.avaliacao_deteccao import (
        avaliar_deteccao,
        carregar_verdades,
        ler_predicoes_jsonl,
    )
    from video.dataset import avaliar_gate

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--imagens", type=Path, required=True, help="pasta de imagens")
    parser.add_argument(
        "--labels", type=Path, required=True, help="pasta de labels .txt YOLO (ground truth)"
    )
    origem = parser.add_mutually_exclusive_group(required=True)
    origem.add_argument("--predicoes", type=Path, help="JSONL de predições já calculadas")
    origem.add_argument("--pesos", type=str, help="pesos YOLO para gerar as predições na hora")
    parser.add_argument(
        "--conf", type=float, default=0.25, help="limiar de confiança ao rodar o modelo"
    )
    parser.add_argument("--device", help="cpu, mps, cuda index etc. (com --pesos)")
    parser.add_argument(
        "--iou", type=float, default=0.5, help="limiar de IoU do acerto (PASCAL VOC)"
    )
    parser.add_argument("--minimo", type=float, default=0.5, help="mAP@0.5 mínimo do gate")
    parser.add_argument(
        "--classes",
        nargs="+",
        type=int,
        default=None,
        help="ids de classe a avaliar (padrão: taxonomia do projeto, só 'person' = 0)",
    )
    parser.add_argument("--output", type=Path, help="salvar o relatório JSON neste caminho")
    args = parser.parse_args(argv)

    try:
        if args.pesos:
            predicoes, classes = _predicoes_de_pesos(args, args.classes)
        else:
            classes = tuple(args.classes) if args.classes else (0,)
            predicoes = ler_predicoes_jsonl(args.predicoes)
        verdades = carregar_verdades(args.imagens, args.labels, classes=classes)
    except (FileNotFoundError, ValueError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1

    resultado = avaliar_deteccao(predicoes, verdades, limiar_iou=args.iou)
    gate = avaliar_gate(
        mapa_50=resultado.mapa_50,
        minimo=args.minimo,
        conjunto=str(args.imagens),
        pesos=args.pesos or "",
    )
    payload = {"avaliacao": resultado.para_dict(), "gate": gate.para_dict()}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if gate.aprovado else 1


def _predicoes_de_pesos(args: argparse.Namespace, classes: list[int] | None):
    from video.avaliacao_deteccao import predicoes_de_detector
    from video.dataset import CLASSES_PADRAO
    from video.detector import ConfiguracaoDetector, DetectorYolo

    config = ConfiguracaoDetector(
        pesos=args.pesos, confianca=args.conf, dispositivo=args.device
    )
    detector = DetectorYolo(config, rastrear=False)
    if classes is None:
        nomes = detector.nomes_classe
        classes = [i for i, nome in nomes.items() if nome in CLASSES_PADRAO]
    predicoes = predicoes_de_detector(detector, args.imagens, classes=tuple(classes))
    return predicoes, tuple(classes)


if __name__ == "__main__":
    raise SystemExit(main())
