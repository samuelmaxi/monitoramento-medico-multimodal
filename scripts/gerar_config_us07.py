"""Gera um arquivo de configuração da US07 para um vídeo, a partir de um template.

O caso comum é reutilizar um config conhecido (ex.: ``config/exemplo_us07.json``)
e só trocar o vídeo, o identificador da fonte e os caminhos de saída. O detector,
as classes de interesse, as ROIs e o monitor são copiados do template — não há
número mágico reescrito aqui.

Uso típico (executar a partir da raiz do repositório)::

    uv run python scripts/gerar_config_us07.py \
      --video conteudos/videos/B_D_0003.mp4 \
      --template config/exemplo_us07.json \
      --saida config/B_D_0003.json

    uv run python scripts/rodar_us07_video.py config/B_D_0003.json --max-frames 100

Por padrão o script abre o vídeo com OpenCV para validar que ele existe e para
gravar o ``fonte.fps`` real (``--sem-metadados`` pula essa leitura). O arquivo
gerado é validado com :meth:`video.Configuracao.de_dict` **antes** de ser escrito:
um config inválido nunca chega ao disco.

Caminhos relativos do template (``fonte.video``, ``eventos.caminho_jsonl`` e a
visualização) são rebaseados para a pasta de saída do novo config, de modo que
continuem apontando para o mesmo alvo independentemente de onde o arquivo for
salvo. O ``patient_id`` do template é preservado; use ``--patient-id`` para
trocar (precisa ser o pseudônimo ``pt_<24 hex>`` do contrato da US04).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

EXTENSOES_VIDEO = (".mp4", ".avi", ".mov", ".mkv", ".m4v")
TEMPLATE_PADRAO = Path("config/exemplo_us07.json")
CARACTERES_ID = re.compile(r"[^0-9A-Za-z._-]+")


def main(argv: list[str] | None = None) -> int:
    raiz = Path(__file__).resolve().parent.parent
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))

    from video import Configuracao, ErroDeConfiguracao, ErroDeVideo, LeitorVideo

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True, help="vídeo alvo do config")
    parser.add_argument(
        "--template",
        type=Path,
        default=TEMPLATE_PADRAO,
        help=f"config a copiar (default: {TEMPLATE_PADRAO})",
    )
    parser.add_argument(
        "--saida",
        type=Path,
        help="arquivo de config a gerar (default: config/<stem>.json)",
    )
    parser.add_argument(
        "--id",
        dest="id_fonte",
        default=None,
        help="fonte.id/source_id (default: nome do vídeo sem extensão)",
    )
    parser.add_argument(
        "--patient-id",
        default=None,
        help="pseudônimo pt_<24 hex> (default: preserva o do template)",
    )
    parser.add_argument(
        "--jsonl",
        type=Path,
        help="JSONL de eventos (default: eventos_<id>.jsonl na pasta do template)",
    )
    parser.add_argument("--forcar", action="store_true", help="sobrescreve a saída")
    parser.add_argument(
        "--sem-metadados",
        action="store_true",
        help="não abre o vídeo com OpenCV (não valida codec nem lê fps)",
    )
    args = parser.parse_args(argv)

    video = args.video.expanduser().resolve()
    template = args.template.expanduser()
    if not template.is_file():
        parser.error(f"template não encontrado: {template}")
    if not video.is_file():
        parser.error(f"vídeo não encontrado: {video}")

    id_fonte = _id_fonte(args.id_fonte, video)
    saida = (args.saida or Path("config") / f"{id_fonte}.json").expanduser()
    if saida.exists() and not args.forcar:
        parser.error(f"saída já existe: {saida} (use --forcar para sobrescrever)")

    try:
        dados: dict[str, Any] = json.loads(template.read_text(encoding="utf-8"))
    except json.JSONDecodeError as erro:
        parser.error(f"{template} não é JSON válido: {erro}")
    if not isinstance(dados, dict):
        parser.error(f"{template} precisa conter um objeto no topo")

    base_template = template.resolve().parent
    base_saida = saida.resolve().parent

    if not args.sem_metadados:
        try:
            with LeitorVideo(video) as leitor:
                metadados = leitor.metadados
        except ErroDeVideo as erro:
            print(f"erro: {erro}", file=sys.stderr)
            return 2
        dados.setdefault("fonte", {})["fps"] = metadados.fps

    _aplicar_fonte(dados, id_fonte=id_fonte, patient_id=args.patient_id)
    _aplicar_caminhos(
        dados,
        video=video,
        base_template=base_template,
        base_saida=base_saida,
        id_fonte=id_fonte,
        jsonl=args.jsonl,
    )

    try:
        config = Configuracao.de_dict(dados, base=base_saida)
    except ErroDeConfiguracao as erro:
        print(f"erro: config gerado inválido: {erro}", file=sys.stderr)
        return 2

    _gravar(saida, dados)
    print(json.dumps(config.resumo(), ensure_ascii=False, indent=2))
    print(f"\nconfig gravado em {saida}")
    return 0


def _id_fonte(valor: str | None, video: Path) -> str:
    """Identificador da fonte: o pedido ou o nome do vídeo, sempre sanitizado."""
    base = valor.strip() if valor else video.stem
    limpo = CARACTERES_ID.sub("_", base).strip("_")
    if not limpo:
        raise SystemExit(f"não foi possível derivar um id da fonte de {video}")
    return limpo


def _aplicar_fonte(
    dados: dict[str, Any],
    *,
    id_fonte: str,
    patient_id: str | None,
) -> None:
    """Ajusta o bloco ``fonte`` sem descartar os demais campos do template."""
    fonte = dados.setdefault("fonte", {})
    if not isinstance(fonte, dict):
        raise SystemExit("bloco 'fonte' do template precisa ser um objeto")
    fonte["id"] = id_fonte
    if patient_id:
        fonte["patient_id"] = patient_id


def _aplicar_caminhos(
    dados: dict[str, Any],
    *,
    video: Path,
    base_template: Path,
    base_saida: Path,
    id_fonte: str,
    jsonl: Path | None,
) -> None:
    """Rebaseia os caminhos do template e define a saída do vídeo e do JSONL."""
    fonte = dados.setdefault("fonte", {})
    fonte["video"] = _relativo(video, base_saida)

    if jsonl is not None:
        destino_jsonl = jsonl.expanduser()
        if not destino_jsonl.is_absolute():
            destino_jsonl = Path.cwd() / destino_jsonl
    else:
        eventos = dados.get("eventos") or {}
        original = eventos.get("caminho_jsonl")
        if original:
            pasta = (base_template / str(original)).parent
        else:
            pasta = base_template.parent / "saida" / "video"
        destino_jsonl = pasta / f"eventos_{id_fonte}.jsonl"

    eventos = dados.get("eventos")
    if isinstance(eventos, dict) or eventos is None:
        eventos = dict(eventos or {})
        eventos["caminho_jsonl"] = _relativo(destino_jsonl, base_saida)
        dados["eventos"] = eventos

    visualizacao = dados.get("visualizacao")
    if isinstance(visualizacao, dict):
        for chave in ("caminho_video", "caminho_imagem"):
            valor = visualizacao.get(chave)
            if valor:
                visualizacao[chave] = _relativo(
                    base_template / str(valor), base_saida
                )


def _relativo(alvo: Path, base: Path) -> str:
    """Caminho de ``alvo`` relativo a ``base``, em formato POSIX e estável."""
    destino = alvo if alvo.is_absolute() else (Path.cwd() / alvo)
    try:
        return Path(os.path.relpath(destino.resolve(), base.resolve())).as_posix()
    except ValueError:  # pragma: no cover — drives diferentes no Windows
        return str(destino.resolve())


def _gravar(caminho: Path, dados: dict[str, Any]) -> None:
    """Grava o JSON de forma atômica (temp + replace) para não deixar meio-arquivo."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_name(f".{caminho.name}.tmp")
    temporario.write_text(
        json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporario.replace(caminho)


if __name__ == "__main__":
    raise SystemExit(main())
