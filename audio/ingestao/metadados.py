"""Metadados por áudio (US10): duração, taxa de amostragem, canais, origem, licença e saídas.

Os caminhos ficam relativos à pasta de saída e o arquivo de origem aparece só pelo nome (ou pelo
caminho relativo à pasta de entrada): nada de ``C:\\Users\\...`` nos registros.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STATUS_OK = "ok"
STATUS_ERRO = "erro"


@dataclass(frozen=True)
class MetadadosAudio:
    audio_id: str
    arquivo: str
    status: str
    erro_tipo: str | None
    erro: str | None
    sha256: str | None
    tamanho_bytes: int | None
    formato_origem: str | None
    codec_origem: str | None
    decodificador: str | None
    duracao_s: float | None
    taxa_amostragem_hz: int | None
    canais: int | None
    origem: str
    licenca: str
    convertido_uri: str | None
    convertido_duracao_s: float | None
    convertido_taxa_hz: int | None
    convertido_canais: int | None
    convertido_codificacao: str | None
    features_uri: str | None
    features_duracao_s: float | None
    silencio_removido_s: float | None
    ganho_aplicado_db: float | None
    original_preservado: bool | None
    versao_config: str
    processado_em: str

    def para_dict(self) -> dict[str, Any]:
        return asdict(self)


COLUNAS = tuple(campo.name for campo in fields(MetadadosAudio))


def sha256_arquivo(caminho: Path) -> str:
    """SHA-256 em blocos de 1 MB (prova que o original não mudou)."""
    resumo = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            resumo.update(bloco)
    return resumo.hexdigest()


def agora_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def uri_relativa(base: Path, caminho: Path) -> str:
    """Caminho de ``caminho`` relativo a ``base``, com barras normais."""
    try:
        return caminho.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return caminho.name


def gravar_json(metadados: MetadadosAudio, caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(
        json.dumps(metadados.para_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def gravar_csv(registros: list[MetadadosAudio], caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=COLUNAS)
        escritor.writeheader()
        for registro in registros:
            escritor.writerow(registro.para_dict())
