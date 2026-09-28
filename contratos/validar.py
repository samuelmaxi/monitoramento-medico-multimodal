"""Valida arquivos de eventos contra o contrato.

    python -m contratos.validar saida/eventos.jsonl video/saida/*.json

Aceita ``.jsonl`` (um evento por linha) e ``.json`` (um objeto ou uma lista).
Sai com código 1 se algum evento for inválido. Use no CI e nos testes de
cada módulo para garantir que todos emitem no formato da US04.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from pydantic import ValidationError

from .evento import EventoAchado


@dataclass(frozen=True)
class ErroValidacao:
    origem: str
    mensagem: str

    def __str__(self) -> str:
        return f"{self.origem}: {self.mensagem}"


def _registros(caminho: Path) -> Iterator[tuple[str, Any]]:
    if caminho.suffix == ".jsonl":
        for numero, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), start=1):
            if linha.strip():
                yield f"{caminho}:{numero}", linha
        return
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    if isinstance(dados, list):
        for indice, item in enumerate(dados):
            yield f"{caminho}[{indice}]", item
    else:
        yield str(caminho), dados


def _resumir(erro: ValidationError) -> str:
    partes = []
    for detalhe in erro.errors():
        local = ".".join(str(p) for p in detalhe["loc"]) or "(evento)"
        partes.append(f"{local}: {detalhe['msg']}")
    return "; ".join(partes)


def validar_arquivo(caminho: str | Path) -> tuple[list[EventoAchado], list[ErroValidacao]]:
    caminho = Path(caminho)
    validos: list[EventoAchado] = []
    erros: list[ErroValidacao] = []
    try:
        registros = list(_registros(caminho))
    except (OSError, json.JSONDecodeError) as exc:
        return [], [ErroValidacao(str(caminho), f"não foi possível ler: {exc}")]
    for origem, registro in registros:
        try:
            if isinstance(registro, str):
                validos.append(EventoAchado.model_validate_json(registro))
            else:
                validos.append(EventoAchado.model_validate(registro))
        except ValidationError as exc:
            erros.append(ErroValidacao(origem, _resumir(exc)))
    return validos, erros


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("arquivos", nargs="+", type=Path)
    args = parser.parse_args(argv)

    total_validos = 0
    todos_erros: list[ErroValidacao] = []
    for arquivo in args.arquivos:
        validos, erros = validar_arquivo(arquivo)
        total_validos += len(validos)
        todos_erros.extend(erros)

    for erro in todos_erros:
        print(f"INVÁLIDO {erro}", file=sys.stderr)
    print(f"{total_validos} evento(s) válido(s), {len(todos_erros)} inválido(s).")
    return 1 if todos_erros else 0


if __name__ == "__main__":
    raise SystemExit(main())
