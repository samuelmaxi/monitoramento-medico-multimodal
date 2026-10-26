import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, UploadFile

from app.config import (
    TRANSCRIPTION_OUTPUT_DIR,
    require_azure_speech_endpoint,
    require_azure_speech_key,
)
from app.schemas import TranscricaoResponse
from app.transcription_client import FORMATOS_SUPORTADOS, TranscricaoError
from app.transcription_service import transcrever_e_salvar

router = APIRouter()


@router.post("/transcription", response_model=TranscricaoResponse)
def transcrever_audio(audio_file: UploadFile) -> dict:
    extension = (audio_file.filename or "").rsplit(".", 1)[-1].lower()
    if extension not in FORMATOS_SUPORTADOS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Formato de áudio não suportado pelo Azure Speech: "
                f"'{extension}'. Formatos aceitos: {sorted(FORMATOS_SUPORTADOS)}."
            ),
        )

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

    try:
        return transcrever_e_salvar(
            caminho_audio,
            pasta,
            endpoint=require_azure_speech_endpoint(),
            chave=require_azure_speech_key(),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except TranscricaoError as exc:
        raise _erro_transcricao(exc, caminho_audio) from exc


def _erro_transcricao(exc: TranscricaoError, caminho_audio: Path) -> HTTPException:
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
