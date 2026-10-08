"""Executa o pipeline US07 sobre uma fonte descrita em JSON/TOML.

Modos de uso::

    # vídeo da configuração (fonte.video)
    python scripts/rodar_us07_video.py config/exemplo_us07.json

    # um vídeo específico, sobrescrevendo fonte.video
    python scripts/rodar_us07_video.py config/exemplo_us07.json --video caminho/video.mp4

    # todos os vídeos de uma pasta (mp4/avi/mov/mkv), um JSONL por vídeo
    python scripts/rodar_us07_video.py config/exemplo_us07.json --videos-dir conteudos/videos

Com ``--video``/``--videos-dir``, o ``source_id`` de cada evento vira o nome do
arquivo (stem) e os eventos vão para ``eventos_<stem>.jsonl`` no diretório de
saída da configuração. No modo lote o detector YOLO é carregado uma única vez
e o tracker é reiniciado entre vídeos; vídeos com falha são pulados e o exit
code fica 1 ao final.

Todo vídeo processado gera seu JSONL — quando não há evento de entrada/saída,
o pipeline grava um registro ``sem_achados`` (severidade ``info``) com o resumo
do processo (quadros lidos, detecções, duração) — e o relatório consolidado é
gravado em ``saida/video/relatorio_us07.json`` (troque com ``--saida-relatorio``).
No modo config única o JSONL configurado é recriado a cada execução (execuções
reproduzíveis).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from video import Configuracao

EXTENSOES_VIDEO = (".mp4", ".avi", ".mov", ".mkv", ".m4v")


def main(argv: list[str] | None = None) -> int:
    raiz = Path(__file__).resolve().parent.parent
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))

    from contratos import EmissorJsonl
    from video import Configuracao, DetectorYolo, PipelineAreaCritica
    from video.visualizacao import GravadorVideo

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="arquivo de configuração JSON ou TOML")
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument(
        "--video",
        type=Path,
        help="processa este vídeo no lugar do fonte.video da configuração",
    )
    grupo.add_argument(
        "--videos-dir",
        type=Path,
        help="processa todos os vídeos (mp4/avi/mov/mkv) da pasta, um JSONL por vídeo",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        help="limite opcional por vídeo (fumaça/validação)",
    )
    parser.add_argument(
        "--saida-relatorio",
        type=Path,
        help=(
            "onde gravar o relatório consolidado (default: "
            "saida/video/relatorio_us07.json)"
        ),
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR")
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    log = logging.getLogger("us07")

    config = Configuracao.de_arquivo(args.config)
    videos, sobrescreve_fonte = _resolver_videos(config, args, parser)
    if not videos:
        parser.error(f"nenhum vídeo encontrado para processar: {_origem(args)}")

    por_video = sobrescreve_fonte
    dir_saida = _dir_saida(config)
    relatorio = (
        Path(args.saida_relatorio).resolve()
        if args.saida_relatorio is not None
        else dir_saida / "relatorio_us07.json"
    )
    jsonl_config = None
    if not por_video:
        jsonl_config = _caminho_jsonl_config(config)
        if jsonl_config is not None:
            jsonl_config.parent.mkdir(parents=True, exist_ok=True)
            jsonl_config.unlink(missing_ok=True)
    detector = DetectorYolo(config.detector)
    resumos: list[dict[str, object]] = []
    falhas: list[dict[str, str]] = []

    for indice, caminho in enumerate(videos):
        stem = caminho.stem
        config_v = config
        emissor = None
        if por_video:
            config_v = replace(config, fonte=replace(config.fonte, video=caminho, id=stem))
            jsonl = dir_saida / f"eventos_{stem}.jsonl"
            jsonl.parent.mkdir(parents=True, exist_ok=True)
            jsonl.unlink(missing_ok=True)
            emissor = EmissorJsonl(jsonl)
        if indice > 0:
            detector.reiniciar_rastreamento()

        pipeline = PipelineAreaCritica(config_v, detector=detector, emissor=emissor)
        gravador = None
        try:
            caminho_video_anotado = _caminho_video_anotado(config, por_video, stem)
            if config_v.visualizacao.ativa and caminho_video_anotado is not None:
                from video import LeitorVideo

                with LeitorVideo(config_v.video) as leitor:
                    gravador = GravadorVideo(caminho_video_anotado, leitor.metadados).abrir()
            resultado = pipeline.executar(ate_quadro=args.max_frames, escritor_video=gravador)
        except Exception as erro:
            log.error("falha ao processar %s: %s", caminho, erro)
            falhas.append({"video": str(caminho), "erro": str(erro)})
            continue
        finally:
            if gravador is not None:
                gravador.fechar()

        if por_video:
            jsonl.touch(exist_ok=True)

        resumo = resultado.resumo.para_dict()
        log.info(
            "%s: %d quadros, %d eventos (%d entrada/saída)",
            stem,
            resumo["quadros_lidos"],
            resumo["eventos"],
            resumo["entradas"],
        )
        resumos.append({"video": str(caminho), **resumo})

    if not por_video:
        if not resumos:
            _gravar_relatorio(relatorio, {"mensagem": "nenhum vídeo processado"})
            return 1
        if jsonl_config is not None:
            jsonl_config.touch(exist_ok=True)
        _gravar_relatorio(relatorio, resumos[0])
        print(json.dumps(resumos[0], ensure_ascii=False, indent=2))
        return 0

    relatorio_dados: dict[str, object] = {
        "videos_processados": len(resumos),
        "videos_com_falha": len(falhas),
        "eventos_totais": sum(int(r["eventos"]) for r in resumos),
        "resumos": resumos,
        "falhas": falhas,
    }
    _gravar_relatorio(relatorio, relatorio_dados)
    print(json.dumps(relatorio_dados, ensure_ascii=False, indent=2))
    return 1 if falhas else 0


def _resolver_videos(
    config: Configuracao, args: argparse.Namespace, parser: argparse.ArgumentParser
) -> tuple[list[Path], bool]:
    """Devolve a lista de vídeos e se a fonte foi sobrescrita pelo usuário."""
    if args.video is not None:
        caminho = args.video.expanduser().resolve()
        if not caminho.is_file():
            parser.error(f"vídeo não encontrado: {caminho}")
        return [caminho], True
    if args.videos_dir is not None:
        pasta = args.videos_dir.expanduser().resolve()
        if not pasta.is_dir():
            parser.error(f"pasta não encontrada: {pasta}")
        videos = sorted(
            p.resolve()
            for p in pasta.iterdir()
            if p.is_file() and p.suffix.lower() in EXTENSOES_VIDEO
        )
        return videos, True
    return [config.video], False


def _origem(args: argparse.Namespace) -> str:
    if args.video is not None:
        return str(args.video)
    if args.videos_dir is not None:
        return str(args.videos_dir)
    return "fonte.video"


def _dir_saida(config: Configuracao) -> Path:
    """Pai do JSONL configurado (ex.: saida/video); default saida/video."""
    jsonl = config.caminho_resolvido(config.evento.caminho_jsonl)
    if jsonl is None:
        return Path("saida/video").resolve()
    return jsonl.parent.resolve()


def _caminho_jsonl_config(config: Configuracao) -> Path | None:
    """JSONL da configuração no modo 'vídeo da config', se a emissão estiver ligada."""
    return config.caminho_resolvido(config.evento.caminho_jsonl)


def _gravar_relatorio(caminho: Path, dados: dict[str, object]) -> None:
    """Persiste o relatório final em arquivo (sempre gerado, mesmo sem eventos)."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(
        json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _caminho_video_anotado(
    config: Configuracao, por_video: bool, stem: str
) -> Path | None:
    """No modo lote, deriva video_anotado_<stem>.mp4 do caminho da configuração."""
    if not config.visualizacao.ativa:
        return None
    configurado = config.caminho_resolvido(config.visualizacao.caminho_video)
    if configurado is None:
        return None
    if not por_video:
        return configurado
    return configurado.parent / f"video_anotado_{stem}.mp4"


if __name__ == "__main__":
    raise SystemExit(main())
