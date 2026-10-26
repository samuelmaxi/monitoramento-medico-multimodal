"""Transcreve um áudio e grava o JSON da transcrição (US11).

Se já existir um JSON para o mesmo áudio (mesmo SHA-256) e as mesmas opções,
ele é reaproveitado e a API não é chamada de novo. `audio_id`, `patient_id` e
`recorded_at` não mudam a transcrição: no reaproveitamento, os informados
substituem os gravados.
"""

import hashlib
import json
import os
from pathlib import Path

from app.transcription_client import transcrever_arquivo
from app.transcription_format import SCHEMA_VERSION, formatar_transcricao, validar_metadados

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
    audio_id: str | None = None,
    patient_id: str | None = None,
    recorded_at: str | None = None,
    **opcoes_cliente,
) -> dict:
    """Transcreve `audio_path` e grava `<saida_dir>/<nome>.json`.

    `audio_id` padrão é o nome do arquivo sem extensão, quando ele serve de ID.
    O áudio de origem não é movido nem apagado. Metadados inválidos levantam
    `ValueError` antes da chamada à API. Se a API falhar, a exceção
    `TranscricaoError` sobe sem que o JSON anterior seja alterado.
    """
    recorded_at = validar_metadados(audio_id, recorded_at)
    sha256 = sha256_arquivo(audio_path)
    destino = saida_dir / f"{audio_path.stem}.json"

    existente = _ler_se_compativel(destino, sha256, usar_frases, max_speakers, medico_falante)
    if existente is not None:
        informados = {"audio_id": audio_id, "patient_id": patient_id, "recorded_at": recorded_at}
        if existente.get("audio_id") is None and audio_id is None:
            informados["audio_id"] = _audio_id_padrao(audio_path)
        novos = {k: v for k, v in informados.items() if v is not None and existente.get(k) != v}
        if novos or not informados.keys() <= existente.keys():
            # JSON anterior ao schema 1.1 não tem os campos de metadados.
            existente = {**{k: None for k in informados}, **existente, **novos, "schema_version": SCHEMA_VERSION}
            _gravar_json(destino, existente)
        return existente

    frases = carregar_frases_medicas() if usar_frases else None
    resposta = transcrever_arquivo(
        audio_path,
        endpoint=endpoint,
        chave=chave,
        frases=frases,
        max_speakers=max_speakers,
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
        audio_id=audio_id or _audio_id_padrao(audio_path),
        patient_id=patient_id,
        recorded_at=recorded_at,
    )
    _gravar_json(destino, resultado)
    return resultado


def _audio_id_padrao(audio_path: Path) -> str | None:
    try:
        validar_metadados(audio_path.stem, None)
    except ValueError:
        return None
    return audio_path.stem


def _ler_se_compativel(
    destino: Path,
    sha256: str,
    usar_frases: bool,
    max_speakers: int,
    medico_falante: int | None,
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
    )
    return existente if compativel else None


def _gravar_json(destino: Path, documento: dict) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(destino.name + ".tmp")
    temporario.write_text(json.dumps(documento, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporario, destino)
