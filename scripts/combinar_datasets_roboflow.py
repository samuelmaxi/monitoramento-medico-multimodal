from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from video.roboflow_dataset import DatasetOrigem


def _raiz_repo() -> None:
    raiz = Path(__file__).resolve().parent.parent
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))


def _datasets(valores: list[str]) -> list[DatasetOrigem]:
    from video.roboflow_dataset import DatasetOrigem

    resultado = []
    for valor in valores:
        tag, separador, caminho = valor.partition("=")
        if not separador or not tag or not caminho:
            raise ValueError(f"dataset inválido {valor!r}; use TAG=PASTA_OU_DATA_YAML")
        resultado.append(DatasetOrigem(tag=tag, caminho=Path(caminho)))
    if len({item.tag for item in resultado}) != len(resultado):
        raise ValueError("tags de dataset repetidas")
    return resultado


def _mapas(valores: list[str], tags: set[str]) -> dict[str, dict[str, str]]:
    resultado: dict[str, dict[str, str]] = {tag: {} for tag in tags}
    for valor in valores:
        tag_classe, separador, destino = valor.partition("=")
        tag, separador_tag, origem = tag_classe.partition(":")
        if not separador or not separador_tag or not tag or not origem or not destino:
            raise ValueError(
                f"mapeamento inválido {valor!r}; use TAG:NOME_EXATO_DA_CLASSE=CLASSE_US07"
            )
        if tag not in resultado:
            raise ValueError(f"tag não informada em --dataset: {tag}")
        if origem in resultado[tag]:
            raise ValueError(f"mapeamento repetido para {tag}:{origem}")
        resultado[tag][origem] = destino
    return resultado


def main(argv: list[str] | None = None) -> int:
    _raiz_repo()
    parser = argparse.ArgumentParser(
        description="Inspeciona exports Roboflow YOLOv8 ou combina em dataset médico US07."
    )
    sub = parser.add_subparsers(dest="comando", required=True)
    inspecionar = sub.add_parser("inspecionar", help="mostra classes disponíveis nos data.yaml")
    inspecionar.add_argument("--dataset", action="append", required=True, metavar="TAG=PASTA")
    combinar = sub.add_parser("combinar", help="combina datasets com mapeamentos explícitos")
    combinar.add_argument("--dataset", action="append", required=True, metavar="TAG=PASTA")
    combinar.add_argument(
        "--map",
        action="append",
        required=True,
        metavar="TAG:CLASSE_ORIGEM=CLASSE_US07",
    )
    combinar.add_argument("--destino", type=Path, default=Path("dataset_medico"))
    args = parser.parse_args(argv)

    try:
        from video.roboflow_dataset import analisar_mapeamentos, combinar_datasets_roboflow

        datasets = _datasets(args.dataset)
        if args.comando == "inspecionar":
            print(json.dumps(analisar_mapeamentos(datasets), ensure_ascii=False, indent=2))
            return 0
        mapas = _mapas(args.map, {item.tag for item in datasets})
        resultado = combinar_datasets_roboflow(
            datasets, mapa_classes=mapas, destino=args.destino
        )
        print(json.dumps(resultado.para_dict(), ensure_ascii=False, indent=2))
        return 0
    except (FileNotFoundError, ValueError, OSError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
