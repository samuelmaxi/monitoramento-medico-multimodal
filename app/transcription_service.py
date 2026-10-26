"""Transcreve um áudio e grava o JSON da transcrição (US11).

Se já existir um JSON para o mesmo áudio (mesmo SHA-256) e as mesmas opções,
ele é reaproveitado e a API não é chamada de novo.
"""

import hashlib
import json
import os
from pathlib import Path

from app.transcription_client import transcrever_arquivo
from app.transcription_format import formatar_transcricao

FRASES_MEDICAS_PATH = Path(__file__).with_name("frases_medicas.txt")


def carregar_frases_medicas(caminho: Path = FRASES_MEDICAS_PATH) -> list[str]:
    """Lê a phrase list (uma por linha; linhas vazias e com `#` são ignoradas)."""
    linhas = caminho.read_text(encoding="utf-8").splitlines()
    return [linha.strip() for linha in linhas if linha.strip() and not linha.lstrip().startswith("#")]


def sha256_arquivo(caminho: Path) -> str:
    digest = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


def transcrever_e_salvar(
    audio_path: Path,
    saida_dir: Path,
    *,
    endpoint: str,
    chave: str,
    usar_frases: bool = False,
    max_speakers: int = 2,
    medico_falante: int | None = None,
    locale: str = "pt-BR",
    **opcoes_cliente,
) -> dict:
    """Transcreve `audio_path` e grava `<saida_dir>/<nome>.json`.

    O áudio de origem não é movido nem apagado. Se a API falhar, a exceção
    `TranscricaoError` sobe sem que o JSON anterior seja alterado.
    """
    sha256 = sha256_arquivo(audio_path)
    destino = saida_dir / f"{audio_path.stem}.json"

    existente = _ler_se_compativel(
        destino, sha256, usar_frases, max_speakers, medico_falante, locale
    )
    if existente is not None:
        return existente

    frases = carregar_frases_medicas() if usar_frases else None
    resposta = transcrever_arquivo(
        audio_path,
        endpoint=endpoint,
        chave=chave,
        frases=frases,
        max_speakers=max_speakers,
        locale=locale,
        **opcoes_cliente,
    )
    resultado = formatar_transcricao(
        resposta,
        nome_arquivo=audio_path.name,
        sha256=sha256,
        tamanho_bytes=audio_path.stat().st_size,
        max_speakers=max_speakers,
        frases_medicas=usar_frases,
        medico_falante=medico_falante,
        locale=locale,
    )
    _gravar_json(destino, resultado)
    return resultado


def _ler_se_compativel(
    destino: Path,
    sha256: str,
    usar_frases: bool,
    max_speakers: int,
    medico_falante: int | None,
    locale: str,
) -> dict | None:
    if not destino.is_file():
        return None
    try:
        existente = json.loads(destino.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    compativel = (
        existente.get("arquivo", {}).get("sha256") == sha256
        and existente.get("frases_medicas") == usar_frases
        and existente.get("diarizacao", {}).get("max_falantes") == max_speakers
        and existente.get("medico_falante") == medico_falante
        and existente.get("idioma") == locale
    )
    return existente if compativel else None


def _gravar_json(destino: Path, documento: dict) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(destino.name + ".tmp")
    temporario.write_text(json.dumps(documento, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporario, destino)
