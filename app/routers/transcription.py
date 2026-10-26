import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException, UploadFile

from app.config import (
    TRANSCRIPTION_OUTPUT_DIR,
    require_azure_speech_endpoint,
    require_azure_speech_key,
)
from app.schemas import TranscricaoResponse
from app.transcription_client import FORMATOS_SUPORTADOS, TranscricaoError
from app.transcription_format import validar_metadados
from app.transcription_service import transcrever_e_salvar

router = APIRouter()


@router.post("/transcription", response_model=TranscricaoResponse)
def transcrever_audio(
    audio_file: UploadFile,
    patient_id: str | None = Form(None, description="ID de origem ou pseudônimo pt_… do paciente"),
    recorded_at: str | None = Form(None, description="Início da gravação, ISO-8601 UTC (ex.: 2026-10-01T13:00:00Z)"),
    audio_id: str | None = Form(None, description="ID curto da consulta, sem dados pessoais. Padrão: o job_id"),
    medico_falante: int | None = Form(None, description="Número do falante que é o médico; os demais viram paciente"),
) -> dict:
    extension = validar_formato(audio_file)
    recorded_at = validar_metadados_form(audio_id, recorded_at)
    job_id, caminho_audio = salvar_audio(audio_file, extension)

    try:
        return transcrever(
            caminho_audio,
            audio_id=audio_id or job_id,
            patient_id=patient_id,
            recorded_at=recorded_at,
            medico_falante=medico_falante,
        )
    except TranscricaoError as exc:
        raise erro_transcricao(exc, caminho_audio) from exc


def validar_formato(audio_file: UploadFile) -> str:
    extension = (audio_file.filename or "").rsplit(".", 1)[-1].lower()
    if extension not in FORMATOS_SUPORTADOS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Formato de áudio não suportado pelo Azure Speech: "
                f"'{extension}'. Formatos aceitos: {sorted(FORMATOS_SUPORTADOS)}."
            ),
        )
    return extension


def validar_metadados_form(audio_id: str | None, recorded_at: str | None) -> str | None:
    """Valida antes de gravar o áudio; devolve `recorded_at` normalizado."""
    try:
        return validar_metadados(audio_id, recorded_at)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def salvar_audio(audio_file: UploadFile, extension: str) -> tuple[str, Path]:
    """Grava o áudio em `<TRANSCRIPTION_OUTPUT_DIR>/<job_id>/` e devolve (job_id, caminho)."""
    job_id = f"transcription-{uuid4().hex[:12]}"
    pasta = Path(TRANSCRIPTION_OUTPUT_DIR) / job_id
    # O áudio é gravado antes da chamada à API: uma falha não pode perdê-lo.
    caminho_audio = pasta / f"audio.{extension}"
    try:
        pasta.mkdir(parents=True)
        with caminho_audio.open("wb") as destino:
            shutil.copyfileobj(audio_file.file, destino)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Não foi possível salvar o áudio de origem: {exc}"
        ) from exc
    return job_id, caminho_audio


def transcrever(caminho_audio: Path, **opcoes) -> dict:
    """Transcreve e grava `audio.json` ao lado do áudio. `TranscricaoError` sobe."""
    try:
        return transcrever_e_salvar(
            caminho_audio,
            caminho_audio.parent,
            endpoint=require_azure_speech_endpoint(),
            chave=require_azure_speech_key(),
            **opcoes,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def erro_transcricao(exc: TranscricaoError, caminho_audio: Path) -> HTTPException:
    preservado = f" O áudio de origem foi preservado em '{caminho_audio}'."

    if exc.status_code in (401, 403):
        return HTTPException(
            status_code=exc.status_code,
            detail=(
                "Credenciais Azure Speech inválidas ou sem permissão. "
                "Verifique AZURE_SPEECH_KEY e AZURE_SPEECH_REGION no .env." + preservado
            ),
        )
    if exc.status_code == 429:
        return HTTPException(
            status_code=429,
            detail="Azure está limitando as requisições; tente novamente em alguns instantes."
            + preservado,
        )
    if exc.status_code is None:
        return HTTPException(
            status_code=504,
            detail=(
                f"Tempo esgotado ou falha de conexão com o Azure Speech após "
                f"{exc.tentativas} tentativas." + preservado
            ),
        )
    return HTTPException(
        status_code=502, detail=f"Erro do Azure Speech: {exc}" + preservado
    )
