"""Processa um lote de transcrições da US11: termos críticos e sentimento (US13).

    python -m audio.text.run --input outputs/transcripts --config audio/text/config.yaml

Para cada transcrição grava ``<output.dir>/<audio_id>.json`` (lista de
``EventoAchado``, valida com ``python -m contratos.validar``) e
``<output.details_dir>/<audio_id>.json`` (entidades, asserções, descartes e
sentimento por sentença). Os eventos também são publicados no barramento local
(``EVENTOS_JSONL``, padrão ``saida/eventos.jsonl``), onde o stub de alertas
recebe os de severidade alta. Uma transcrição com erro é registrada no log e o
lote segue; erro de credencial interrompe o lote.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Optional

from dotenv import load_dotenv

from contratos import BarramentoLocal

from .alerts import assinar_alertas
from .analyze import analyze_transcript
from .config import OutputConfig, TextConfig, load_config
from .language_client import LanguageAuthError, LanguageClient
from .terms import TermMatcher
from .transcript import load_transcript

log = logging.getLogger("audio.text.run")


def find_transcripts(inputs: Iterable[Path], config: TextConfig) -> list[Path]:
    """Arquivos de transcrição nas entradas (arquivos ou pastas, recursivo)."""
    encontrados: list[Path] = []
    for item in inputs:
        candidatos = sorted(p for p in item.rglob("*") if p.is_file()) if item.is_dir() else [item]
        encontrados.extend(p for p in candidatos if p.suffix.lower() in config.input_extensions)
    return encontrados


def _gravar_json(caminho: Path, dados: object) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _setup_logging(out_dir: Path, verbose: bool) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in (logging.StreamHandler(sys.stderr), logging.FileHandler(out_dir / "run.log", encoding="utf-8")):
        handler.setFormatter(fmt)
        root.addHandler(handler)
    # O SDK da Azure loga cada requisição HTTP em INFO.
    logging.getLogger("azure").setLevel(logging.WARNING)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", nargs="+", required=True, type=Path,
                        help="pasta(s) ou arquivo(s) de transcrição; pastas são varridas recursivamente")
    parser.add_argument("--config", required=True, type=Path, help="caminho do config.yaml")
    parser.add_argument("--limit", type=int, default=None, help="processa no máximo N transcrições")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="substitui output.dir do config (detalhes em <dir>/detalhes); útil por dataset")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    load_dotenv()
    config = load_config(args.config)
    if args.output_dir is not None:
        config = replace(config, output=OutputConfig(dir=args.output_dir, details_dir=args.output_dir / "detalhes"))
    _setup_logging(config.output.dir, args.verbose)

    caminhos: list[Path] = []
    for item in args.input:
        if not item.exists():
            log.error("entrada não encontrada: %s", item)
            continue
        caminhos.extend(find_transcripts([item], config))
    if args.limit is not None:
        caminhos = caminhos[: args.limit]
    if not caminhos:
        log.error("nenhuma transcrição encontrada em %s", ", ".join(map(str, args.input)))
        return 1

    try:
        cliente = LanguageClient.from_env(config.azure)
    except LanguageAuthError as exc:
        log.error("%s", exc)
        return 1

    barramento = BarramentoLocal.do_ambiente()
    assinar_alertas(barramento)
    matcher = TermMatcher(config.critical_terms)
    log.info("termos críticos v%s (%d termos); health=%s, sentimento=%s; eventos em %s",
             config.critical_terms.version, len(config.critical_terms.terms),
             config.azure.health_language, config.azure.sentiment_language, barramento.caminho)

    erros = 0
    total_eventos = 0
    for i, caminho in enumerate(caminhos, 1):
        log.info("[%d/%d] %s", i, len(caminhos), caminho)
        try:
            transcricao = load_transcript(caminho)
            resultado = analyze_transcript(transcricao, config, cliente, barramento, matcher=matcher)
        except LanguageAuthError as exc:
            log.error("%s", exc)
            return 1
        except Exception:  # uma transcrição ruim não derruba o lote
            log.exception("[%d/%d] erro ao processar %s", i, len(caminhos), caminho)
            erros += 1
            continue
        _gravar_json(config.output.dir / f"{resultado.audio_id}.json", [e.para_dict() for e in resultado.events])
        _gravar_json(config.output.details_dir / f"{resultado.audio_id}.json", resultado.details)
        total_eventos += len(resultado.events)

    for falha in barramento.falhas:
        log.error("assinante %s falhou no evento %s: %s", falha.assinante, falha.event_id, falha.erro)
    log.info("concluído: %d transcrição(ões), %d com erro, %d evento(s); requisições à Azure: %s",
             len(caminhos), erros, total_eventos, cliente.requests_sent)
    return 0 if erros == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
