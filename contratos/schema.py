"""Gera o JSON Schema do contrato a partir do modelo pydantic.

    python -m contratos.schema            # (re)gera o arquivo
    python -m contratos.schema --check    # CI: falha se o arquivo estiver desatualizado

O schema inclui as regras do catálogo (event_type por modalidade, fusão com
related_event_ids e sem_achados → info), então consumidores fora do Python
(ex.: um painel em Node.js) validam as mesmas regras com Ajv. O limite de
tamanho (256 KB) não é expressável em JSON Schema e só o pydantic verifica.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .catalogo import SEM_ACHADOS, TIPOS_DE_EVENTO, Modalidade, Severidade
from .evento import SCHEMA_VERSION, EventoAchado

CAMINHO_SCHEMA = Path(__file__).parent / "schema" / "evento_achado.v1.schema.json"
SCHEMA_ID = "https://github.com/tech-challenge-fase-4/contratos/evento_achado.v1.schema.json"


def gerar_schema() -> dict[str, Any]:
    schema = EventoAchado.model_json_schema(mode="validation")
    todos_os_tipos = sorted({t for tipos in TIPOS_DE_EVENTO.values() for t in tipos})

    schema["properties"]["event_type"]["enum"] = todos_os_tipos
    # No fio, o evento sempre carrega a versão do contrato (o pydantic a preenche).
    schema["required"] = ["schema_version", *schema["required"]]
    regras: list[dict[str, Any]] = [
        {
            "if": {"properties": {"modality": {"const": modalidade.value}}, "required": ["modality"]},
            "then": {"properties": {"event_type": {"enum": sorted(tipos)}}},
        }
        for modalidade, tipos in TIPOS_DE_EVENTO.items()
    ]
    regras.append({
        "if": {"properties": {"modality": {"const": Modalidade.FUSAO.value}}, "required": ["modality"]},
        "then": {"required": ["related_event_ids"], "properties": {"related_event_ids": {"minItems": 1}}},
    })
    regras.append({
        "if": {"properties": {"event_type": {"const": SEM_ACHADOS}}, "required": ["event_type"]},
        "then": {"properties": {"severity": {"const": Severidade.INFO.value}}},
    })

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "EventoAchado",
        "description": (
            f"Contrato único de achado/evento do Tech Challenge Fase 4 (v{SCHEMA_VERSION}). "
            "Gerado de contratos/evento.py — não edite à mão."
        ),
        **{k: v for k, v in schema.items() if k not in {"title", "description"}},
        "allOf": regras,
    }


def serializar(schema: dict[str, Any]) -> str:
    return json.dumps(schema, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="só verifica, não grava")
    args = parser.parse_args(argv)

    esperado = serializar(gerar_schema())
    atual = CAMINHO_SCHEMA.read_text(encoding="utf-8") if CAMINHO_SCHEMA.exists() else ""

    if args.check:
        if atual != esperado:
            print(f"{CAMINHO_SCHEMA} está desatualizado. Rode: python -m contratos.schema", file=sys.stderr)
            return 1
        print("Schema em dia.")
        return 0

    CAMINHO_SCHEMA.parent.mkdir(parents=True, exist_ok=True)
    CAMINHO_SCHEMA.write_text(esperado, encoding="utf-8")
    print(f"Schema gravado em {CAMINHO_SCHEMA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
