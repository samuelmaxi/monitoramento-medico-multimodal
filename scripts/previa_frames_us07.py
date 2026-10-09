"""Gera prévias visuais (contact sheets) dos frames do dataset da US07.

Lê o manifesto de frames e monta:

- um contact sheet por cenário (todos os frames únicos, rotulados com vídeo e
  timestamp);
- páginas de contact sheet organizadas por vídeo (uma linha por vídeo de origem);
- um índice CSV com vídeo, cenário, timestamp e marcação de duplicado.

Não cria nem altera rótulos; é apenas inspeção visual.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

LARGURA_TILE = 3.2
ALTURA_TILE = 2.0


def _raiz_repo() -> Path:
    raiz = Path(__file__).resolve().parent.parent
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))
    return raiz


def _resolver_imagem(base: Path, registro: dict) -> Path | None:
    relativo = registro.get("arquivo_imagem")
    if relativo:
        caminho = base / str(relativo)
        if caminho.is_file():
            return caminho
    video_id = str(registro["video_id"])
    nome = str(registro["frame_id"]).split("/")[-1] + ".jpg"
    alternativo = base / "frames" / video_id / nome
    return alternativo if alternativo.is_file() else None


def _imagem(plt, caminho: Path):
    import cv2

    imagem = cv2.imread(str(caminho))
    if imagem is None:
        return None
    return cv2.cvtColor(imagem, cv2.COLOR_BGR2RGB)


def _rotulo(registro: dict) -> str:
    marca = " [dup]" if registro.get("duplicado") else ""
    return f"{registro['video_id']}\nt={float(registro['timestamp_s']):.1f}s{marca}"


def sheet_por_cenario(frames, base: Path, saida: Path, ncols: int = 8) -> Path | None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not frames:
        return None
    nrows = (len(frames) + ncols - 1) // ncols
    figura, eixos = plt.subplots(
        nrows, ncols, figsize=(ncols * LARGURA_TILE, nrows * ALTURA_TILE), squeeze=False
    )
    for indice, registro in enumerate(frames):
        linha, coluna = divmod(indice, ncols)
        eixo = eixos[linha][coluna]
        eixo.axis("off")
        caminho = _resolver_imagem(base, registro)
        if caminho is not None:
            imagem = _imagem(plt, caminho)
            if imagem is not None:
                eixo.imshow(imagem)
        eixo.set_title(_rotulo(registro), fontsize=6)
    for sobra in range(len(frames), nrows * ncols):
        eixos[sobra // ncols][sobra % ncols].axis("off")
    figura.tight_layout()
    saida.parent.mkdir(parents=True, exist_ok=True)
    figura.savefig(saida, dpi=110)
    plt.close(figura)
    return saida


def sheets_por_video(
    frames, base: Path, pasta: Path, *, por_pagina: int = 8, ncols: int = 3
) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    por_video: dict[str, list[dict]] = defaultdict(list)
    for registro in frames:
        por_video[str(registro["video_id"])].append(registro)
    for lista in por_video.values():
        lista.sort(key=lambda r: float(r["timestamp_s"]))
    videos = sorted(por_video)
    gerados: list[Path] = []
    for numero, inicio in enumerate(range(0, len(videos), por_pagina), start=1):
        pagina = videos[inicio : inicio + por_pagina]
        ncols_pagina = max(len(por_video[v]) for v in pagina)
        figura, eixos = plt.subplots(
            len(pagina),
            ncols_pagina,
            figsize=(ncols_pagina * LARGURA_TILE, len(pagina) * ALTURA_TILE),
            squeeze=False,
        )
        for i, video_id in enumerate(pagina):
            for j in range(ncols_pagina):
                eixo = eixos[i][j]
                eixo.axis("off")
                if j < len(por_video[video_id]):
                    registro = por_video[video_id][j]
                    caminho = _resolver_imagem(base, registro)
                    if caminho is not None:
                        imagem = _imagem(plt, caminho)
                        if imagem is not None:
                            eixo.imshow(imagem)
                    eixo.set_title(_rotulo(registro), fontsize=6)
                else:
                    eixo.text(0.5, 0.5, "—", ha="center", va="center", fontsize=8)
            eixos[i][0].set_ylabel(video_id, rotation=0, ha="right", va="center", fontsize=6)
        figura.suptitle(f"Frames por vídeo — página {numero}", fontsize=9)
        figura.tight_layout()
        pasta.mkdir(parents=True, exist_ok=True)
        destino = pasta / f"previa_por_video_pag{numero}.png"
        figura.savefig(destino, dpi=110)
        plt.close(figura)
        gerados.append(destino)
    return gerados


def escrever_indice(frames, saida: Path) -> Path:
    saida.parent.mkdir(parents=True, exist_ok=True)
    with saida.open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.DictWriter(
            arquivo,
            fieldnames=[
                "video_id",
                "frame_id",
                "cenario",
                "condicao",
                "timestamp_s",
                "duplicado",
                "a_hash",
            ],
        )
        escritor.writeheader()
        for registro in frames:
            escritor.writerow(
                {
                    "video_id": registro["video_id"],
                    "frame_id": registro["frame_id"],
                    "cenario": registro.get("cenario"),
                    "condicao": registro.get("condicao"),
                    "timestamp_s": registro["timestamp_s"],
                    "duplicado": registro.get("duplicado"),
                    "a_hash": registro.get("a_hash"),
                }
            )
    return saida


def main(argv: list[str] | None = None) -> int:
    _raiz_repo()
    from video.extracao_frames import ler_manifesto

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destino", type=Path, default=Path("dataset_us07"))
    parser.add_argument(
        "--manifesto",
        type=Path,
        default=None,
        help="caminho do frames_manifest.jsonl (padrão: <destino>/metadata/...)",
    )
    parser.add_argument(
        "--saida", type=Path, default=None, help="pasta de saída (padrão: <destino>/reports/previa)"
    )
    parser.add_argument("--incluir-duplicados", action="store_true")
    parser.add_argument("--por-pagina", type=int, default=8)
    args = parser.parse_args(argv)

    base = args.destino
    manifesto = args.manifesto or base / "metadata" / "frames_manifest.jsonl"
    saida = args.saida or base / "reports" / "previa"
    registros = ler_manifesto(manifesto)
    if not args.incluir_duplicados:
        registros = [r for r in registros if not r.get("duplicado")]

    escrever_indice(registros, saida / "indice_frames.csv")

    por_cenario: dict[str, list[dict]] = defaultdict(list)
    for registro in registros:
        por_cenario[str(registro.get("cenario") or "desconhecido")].append(registro)
    for cenario, lista in sorted(por_cenario.items()):
        lista.sort(key=lambda r: (r["video_id"], float(r["timestamp_s"])))
        sheet_por_cenario(lista, base, saida / f"previa_cenario_{cenario}.png")

    paginas = sheets_por_video(registros, base, saida / "por_video", por_pagina=args.por_pagina)
    print(f"frames: {len(registros)}")
    print(f"cenarios: {sorted(por_cenario)}")
    print(f"paginas por video: {len(paginas)}")
    print(f"saida: {saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
