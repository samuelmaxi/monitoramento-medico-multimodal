"""Executa o pipeline US07 sobre uma fonte descrita em JSON/TOML."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    raiz = Path(__file__).resolve().parent.parent
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))

    from video import Configuracao, PipelineAreaCritica
    from video.visualizacao import GravadorVideo

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="arquivo de configuração JSON ou TOML")
    parser.add_argument("--max-frames", type=int, help="limite opcional para fumaça/validação")
    parser.add_argument(
        "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR")
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    config = Configuracao.de_arquivo(args.config)
    pipeline = PipelineAreaCritica(config)
    gravador = None
    if config.visualizacao.ativa and config.visualizacao.caminho_video:
        from video import LeitorVideo

        with LeitorVideo(config.video) as leitor:
            caminho_saida = config.caminho_resolvido(config.visualizacao.caminho_video)
            assert caminho_saida is not None
            gravador = GravadorVideo(caminho_saida, leitor.metadados).abrir()
    try:
        resultado = pipeline.executar(ate_quadro=args.max_frames, escritor_video=gravador)
    finally:
        if gravador is not None:
            gravador.fechar()

    print(json.dumps(resultado.resumo.para_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
