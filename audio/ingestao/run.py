"""Converte e valida áudios de consultas (US10): WAV PCM 16 kHz mono, sem alterar o original.

    python -m audio.ingestao.run --input gravacoes/ --origem "consulta simulada" --licenca "CC0"

Por áudio, em ``--saida`` (padrão ``saida/audio``): ``<audio_id>/convertido.wav``,
``<audio_id>/features.wav`` (normalizado e sem silêncios longos, só para features) e
``<audio_id>/metadados.json``. Um ``metadados.csv`` resume o lote. Áudio corrompido, vazio ou em
formato não suportado é logado como erro e o lote segue.

Saída: 0 = todos ingeridos; 1 = nada para processar ou configuração inválida; 2 = algum falhou.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import CAMINHO_PADRAO, ConfigIngestao, ErroDeConfiguracao, carregar_config
from .lote import NOME_CSV, Entrada, ResultadoIngestao, ingerir_lote

log = logging.getLogger("audio.ingestao.run")


def listar_arquivos(entradas: Sequence[Path], cfg: ConfigIngestao) -> list[Entrada]:
    """Arquivos a ingerir.

    Uma pasta é percorrida recursivamente pelas extensões da config; um arquivo indicado
    diretamente entra mesmo com extensão desconhecida (vira um erro registrado, não é ignorado).
    """
    arquivos: list[Entrada] = []
    for entrada in entradas:
        if entrada.is_dir():
            achados = sorted(
                p for p in entrada.rglob("*")
                if p.is_file() and p.suffix.lower().lstrip(".") in cfg.validacao.extensoes
            )
            arquivos.extend(Entrada(p, entrada) for p in achados)
        else:
            arquivos.append(Entrada(entrada, entrada.parent))
    return arquivos


def executar(
    entradas: Sequence[Path],
    saida: Path,
    cfg: ConfigIngestao,
    *,
    origem: str | None = None,
    licenca: str | None = None,
) -> list[ResultadoIngestao]:
    return ingerir_lote(listar_arquivos(entradas, cfg), saida, cfg, origem=origem, licenca=licenca)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", "-i", type=Path, nargs="+", required=True,
                        help="arquivos ou pastas de áudio")
    parser.add_argument("--saida", type=Path, default=Path("saida/audio"))
    parser.add_argument("--config", type=Path, default=CAMINHO_PADRAO)
    parser.add_argument("--origem", help="origem da gravação (registrada nos metadados)")
    parser.add_argument("--licenca", help="licença ou base legal de uso (registrada nos metadados)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")

    try:
        cfg = carregar_config(args.config)
    except ErroDeConfiguracao as erro:
        log.error("configuração inválida: %s", erro)
        return 1

    if not listar_arquivos(args.input, cfg):
        log.error("nenhum arquivo de áudio encontrado em %s", ", ".join(map(str, args.input)))
        return 1

    resultados = executar(args.input, args.saida, cfg, origem=args.origem, licenca=args.licenca)
    falhas = [r for r in resultados if not r.ok]
    log.info("%d áudio(s): %d ok, %d com erro. Resumo: %s", len(resultados),
             len(resultados) - len(falhas), len(falhas), args.saida / NOME_CSV)
    return 2 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
