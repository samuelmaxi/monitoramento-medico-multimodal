"""CLI da US07 para construir, validar e treinar o dataset de detecção de pessoas.

Fluxo típico (executar a partir da raiz do repositório)::

    uv run python scripts/dataset_us07.py inventariar
    uv run python scripts/dataset_us07.py extrair
    uv run python scripts/dataset_us07.py montar
    uv run python scripts/dataset_us07.py validar
    uv run python scripts/dataset_us07.py exportar-anotacao
    # anotar em CVAT/Ultralytics e então:
    uv run python scripts/dataset_us07.py importar-anotacoes --de anotavel/labels/train
    uv run python scripts/dataset_us07.py verificar-apto
    uv run python scripts/dataset_us07.py treinar
    uv run python scripts/dataset_us07.py avaliar

Nenhuma etapa inventa anotações: sem labels reais o dataset permanece inapto e o
treino é recusado.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

NOME_MANIFESTO = "frames_manifest.jsonl"
NOME_INVENTARIO_JSON = "inventario.json"
NOME_INVENTARIO_CSV = "inventario.csv"
NOME_FRAMES_DIVISAO = "frames_divisao.jsonl"


def _raiz_repo() -> Path:
    raiz = Path(__file__).resolve().parent.parent
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))
    return raiz


def _classes(valores: Sequence[str] | None) -> list[str]:
    return list(valores) if valores else ["person"]


def _metadata_dir(args: argparse.Namespace) -> Path:
    out = getattr(args, "out", None)
    return Path(out) if out else Path(args.destino) / "metadata"


def _inventario_json(args: argparse.Namespace) -> Path:
    inventario = getattr(args, "inventario", None)
    return Path(inventario) if inventario else _metadata_dir(args) / NOME_INVENTARIO_JSON


def _imprimir(dados: object) -> None:
    print(json.dumps(dados, ensure_ascii=False, indent=2, default=str))


def cmd_inventariar(args: argparse.Namespace) -> int:
    from video.inventario import inventariar, resumo_inventario, salvar_inventario

    itens = inventariar(args.videos)
    if not itens:
        print(f"nenhum vídeo encontrado em {args.videos}", file=sys.stderr)
        return 1
    saida = _metadata_dir(args)
    saida.mkdir(parents=True, exist_ok=True)
    caminhos = salvar_inventario(
        itens,
        caminho_csv=saida / NOME_INVENTARIO_CSV,
        caminho_json=saida / NOME_INVENTARIO_JSON,
    )
    _imprimir({"resumo": resumo_inventario(itens), "arquivos": caminhos})
    return 0


def cmd_extrair(args: argparse.Namespace) -> int:
    from video.extracao_frames import ConfiguracaoExtracao, extrair_lote
    from video.inventario import inventariar, ler_inventario

    inventario = _inventario_json(args)
    if inventario.is_file():
        itens = ler_inventario(inventario)
    else:
        print("inventário ausente; rodando 'inventariar' em memória", file=sys.stderr)
        itens = inventariar(args.videos)

    config = ConfiguracaoExtracao(
        intervalo_s=args.intervalo,
        max_frames=args.max_frames,
        largura_max=args.largura_max,
        detectar_semelhantes=not args.sem_dedup,
        distancia_semelhanca=args.distancia,
        min_frames_por_video=args.min_frames_por_video,
        retomar=not args.sem_retomar,
    )
    relatorio = extrair_lote(
        itens, raiz_videos=args.videos, destino_raiz=args.destino, config=config
    )
    _imprimir(
        {
            "frames": len(relatorio.frames),
            "duplicados": sum(1 for f in relatorio.frames if f.duplicado),
            "videos_processados": relatorio.videos_processados,
            "videos_pulados": relatorio.videos_pulados,
            "videos_com_erro": relatorio.videos_com_erro,
            "falhas": relatorio.falhas,
        }
    )
    return 1 if relatorio.videos_com_erro else 0


def _registros_e_grupos(destino: Path, inventario: Path):
    from video.dataset import agrupar_por_origem, carregar_registros
    from video.inventario import ler_inventario

    registros = carregar_registros(destino / "metadata" / NOME_MANIFESTO)
    hashes: dict[str, list[str]] = {}
    if inventario.is_file():
        for item in ler_inventario(inventario):
            if item.sha256:
                hashes.setdefault(item.chave_grupo, []).append(item.sha256)
    grupos = agrupar_por_origem(registros, hashes_por_grupo=hashes or None)
    return registros, grupos


def cmd_montar(args: argparse.Namespace) -> int:
    from video.dataset import materializar_divisao, planejar_divisao

    destino = Path(args.destino)
    registros, grupos = _registros_e_grupos(destino, _inventario_json(args))
    if not registros:
        print("manifesto de frames vazio; rode 'extrair' antes", file=sys.stderr)
        return 1
    proporcoes = {"train": args.train, "val": args.val, "test": args.test}
    try:
        divisao, avisos = planejar_divisao(grupos, proporcoes=proporcoes, semente=args.semente)
    except ValueError as erro:
        print(str(erro), file=sys.stderr)
        return 2
    resultado = materializar_divisao(
        destino,
        registros,
        grupos,
        divisao,
        classes=_classes(args.classes),
        copiar=not args.symlink,
        avisos=avisos,
    )
    _imprimir(
        {
            "data_yaml": str(resultado.data_yaml),
            "frames_por_split": resultado.por_split,
            "grupos_por_split": resultado.grupos_por_split,
            "avisos": resultado.avisos,
        }
    )
    return 0


def cmd_validar(args: argparse.Namespace) -> int:
    from video.validacao_dataset import salvar_relatorio, validar_dataset

    destino = Path(args.destino)
    relatorio = validar_dataset(destino, classes=_classes(args.classes))
    salvar_relatorio(relatorio, destino / "reports" / "validacao_dataset.json")
    _imprimir(relatorio.para_dict())
    return 0 if relatorio.ok else 1


def cmd_exportar_anotacao(args: argparse.Namespace) -> int:
    from video.anotacoes import exportar_para_anotacao

    destino = Path(args.destino)
    colocacoes = destino / "metadata" / NOME_FRAMES_DIVISAO
    if not colocacoes.is_file():
        print("divisão ausente; rode 'montar' antes", file=sys.stderr)
        return 1
    atribuicoes: dict[str, list[str]] = {}
    for linha in colocacoes.read_text(encoding="utf-8").splitlines():
        if not linha.strip():
            continue
        registro = json.loads(linha)
        atribuicoes.setdefault(registro["split"], []).append(registro["frame_id"])
    saida = args.saida or destino / "anotavel"
    resultado = exportar_para_anotacao(
        destino, saida, atribuicoes=atribuicoes, classes=_classes(args.classes)
    )
    _imprimir(resultado.para_dict())
    return 0


def cmd_importar_anotacoes(args: argparse.Namespace) -> int:
    from video.anotacoes import importar_anotacoes

    importados = importar_anotacoes(args.de_, args.para)
    _imprimir({"importados": len(importados), "arquivos": importados})
    return 0


def cmd_gerar_data_yaml(args: argparse.Namespace) -> int:
    from video.dataset import gerar_data_yaml

    caminho = gerar_data_yaml(Path(args.destino), classes=_classes(args.classes))
    print(str(caminho))
    return 0


def cmd_verificar_apto(args: argparse.Namespace) -> int:
    from video.anotacoes import ProblemaAnotacao
    from video.dataset import avaliar_gate, verificar_aptidao

    destino = Path(args.destino)
    problemas: list[ProblemaAnotacao] = []
    relatorio = Path(args.validacao)
    if relatorio.is_file():
        dados = json.loads(relatorio.read_text(encoding="utf-8"))
        problemas = [
            ProblemaAnotacao(
                p.get("caminho", ""), p.get("severidade", "erro"), p.get("mensagem", "")
            )
            for p in dados.get("erros", [])
        ]
    avaliacao = verificar_aptidao(
        destino, classes=_classes(args.classes), problemas_estruturais=problemas
    )
    gate = avaliar_gate(mapa_50=None)
    _imprimir({"aptidao": avaliacao.para_dict(), "gate": gate.para_dict()})
    return 0 if avaliacao.apto else 1


def cmd_treinar(args: argparse.Namespace) -> int:
    from video.dataset import verificar_aptidao

    destino = Path(args.destino)
    data_yaml = destino / "data.yaml"
    avaliacao = verificar_aptidao(destino, classes=_classes(args.classes))
    if not avaliacao.apto:
        print("dataset inapto para treino; anote os frames primeiro", file=sys.stderr)
        _imprimir(avaliacao.para_dict())
        return 2
    from ultralytics import YOLO

    modelo = YOLO(args.modelo)
    resultados = modelo.train(
        data=str(data_yaml),
        epochs=args.epochs,
        imgsz=args.imgsz,
        device=args.device,
        project=args.project,
        name=args.name,
    )
    _imprimir({"save_dir": str(getattr(resultados, "save_dir", ""))})
    return 0


def cmd_avaliar(args: argparse.Namespace) -> int:
    from video.anotacoes import salvar_json
    from video.dataset import avaliar_gate
    from video.metricas import avaliar_pesos

    destino = Path(args.destino)
    metricas = avaliar_pesos(
        args.pesos, destino / "data.yaml", split=args.split, dispositivo=args.device
    )
    gate = avaliar_gate(
        mapa_50=metricas.mapa_50, minimo=args.minimo, conjunto=args.split, pesos=args.pesos
    )
    saida = destino / "reports" / f"avaliacao_{args.split}.json"
    salvar_json(saida, {"metricas": metricas.para_dict(), "gate": gate.para_dict()})
    _imprimir({"metricas": metricas.para_dict(), "gate": gate.para_dict(), "saida": str(saida)})
    return 0 if gate.aprovado else 1


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--destino", default="dataset_us07", help="raiz do dataset de trabalho")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("inventariar", help="inventaria os vídeos com SHA-256 e metadados")
    p.add_argument("--videos", type=Path, default=Path("conteudos/videos"))
    p.add_argument(
        "--out", type=Path, default=None, help="pasta de metadados (padrão: <destino>/metadata)"
    )
    p.set_defaults(func=cmd_inventariar)

    p = sub.add_parser("extrair", help="extrai frames e grava o manifesto")
    p.add_argument("--videos", type=Path, default=Path("conteudos/videos"))
    p.add_argument("--inventario", type=Path, default=None)
    p.add_argument("--intervalo", type=float, default=1.0, help="segundos entre frames")
    p.add_argument("--max-frames", type=int, default=30)
    p.add_argument("--largura-max", type=int, default=None)
    p.add_argument("--distancia", type=int, default=3, help="limiar de deduplicação aHash")
    p.add_argument(
        "--min-frames-por-video",
        type=int,
        default=None,
        help="mínimo de frames únicos por vídeo (promove descartes mais diversos)",
    )
    p.add_argument("--sem-dedup", action="store_true")
    p.add_argument("--sem-retomar", action="store_true")
    p.set_defaults(func=cmd_extrair)

    p = sub.add_parser("montar", help="planeja a divisão sem vazamento e materializa o dataset")
    p.add_argument("--inventario", type=Path, default=None)
    p.add_argument("--classes", nargs="+", default=["person"])
    p.add_argument("--train", type=float, default=0.7)
    p.add_argument("--val", type=float, default=0.2)
    p.add_argument("--test", type=float, default=0.1)
    p.add_argument("--semente", type=int, default=42)
    p.add_argument("--symlink", action="store_true", help="usa links simbólicos em vez de copiar")
    p.set_defaults(func=cmd_montar)

    p = sub.add_parser("validar", help="valida estrutura, labels e vazamentos")
    p.add_argument("--classes", nargs="+", default=["person"])
    p.set_defaults(func=cmd_validar)

    p = sub.add_parser("exportar-anotacao", help="gera uma cópia anotável (CVAT/Ultralytics)")
    p.add_argument(
        "--saida", type=Path, default=None, help="pasta de saída (padrão: <destino>/anotavel)"
    )
    p.add_argument("--classes", nargs="+", default=["person"])
    p.set_defaults(func=cmd_exportar_anotacao)

    p = sub.add_parser("importar-anotacoes", help="importa labels anotados para o dataset")
    p.add_argument("--de", dest="de_", type=Path, required=True)
    p.add_argument("--para", type=Path, required=True)
    p.set_defaults(func=cmd_importar_anotacoes)

    p = sub.add_parser("gerar-data-yaml", help="(re)gera o data.yaml")
    p.add_argument("--classes", nargs="+", default=["person"])
    p.set_defaults(func=cmd_gerar_data_yaml)

    p = sub.add_parser("verificar-apto", help="checa prontidão para treino e estado do gate")
    p.add_argument("--classes", nargs="+", default=["person"])
    p.add_argument(
        "--validacao",
        type=Path,
        default=Path("dataset_us07/reports/validacao_dataset.json"),
    )
    p.set_defaults(func=cmd_verificar_apto)

    p = sub.add_parser("treinar", help="treina YOLO no dataset (exige aptidão)")
    p.add_argument("--modelo", default="yolov8n.pt")
    p.add_argument("--classes", nargs="+", default=["person"])
    p.add_argument("--epochs", type=int, default=50)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--device", default=None)
    p.add_argument("--project", default="runs/detect")
    p.add_argument("--name", default="us07")
    p.set_defaults(func=cmd_treinar)

    p = sub.add_parser("avaliar", help="avalia pesos e aplica o gate mAP@0.5")
    p.add_argument("--pesos", default="yolov8n.pt")
    p.add_argument("--split", default="test", choices=("val", "test", "train"))
    p.add_argument("--device", default=None)
    p.add_argument("--minimo", type=float, default=0.5)
    p.set_defaults(func=cmd_avaliar)

    return parser


def main(argv: list[str] | None = None) -> int:
    _raiz_repo()
    parser = construir_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FileNotFoundError as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
