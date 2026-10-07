import io
import json
import os
import tempfile
import time
from uuid import uuid4

from azure.cognitiveservices.speech import AudioConfig, SpeechRecognizer
from azure.core.exceptions import AzureError, ResourceNotFoundError
from fastapi import APIRouter, HTTPException, UploadFile

from app.azure_clients import get_blob_service_client, get_speech_config
from app.config import require_blob_container_name
from app.errors import translate_client_error
from app.schemas import TranscriptionResultResponse, TranscriptionStartResponse

router = APIRouter()

_SUPPORTED_FORMATS = {"wav", "mp3", "mp4", "flac", "ogg", "webm"}
_INPUT_PREFIX = "transcription-input"
_OUTPUT_PREFIX = "transcription-output"
_JOBS_METADATA = {}


@router.post("/transcription", response_model=TranscriptionStartResponse)
def start_transcription(audio_file: UploadFile) -> TranscriptionStartResponse:
    extension = (audio_file.filename or "").rsplit(".", 1)[-1].lower()
    if extension not in _SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Formato de áudio não suportado pelo Azure Speech: "
                f"'{extension}'. Formatos aceitos: {sorted(_SUPPORTED_FORMATS)}."
            ),
        )

    job_name = f"transcription-{uuid4().hex[:12]}"
    blob_name = f"{_INPUT_PREFIX}/{job_name}.{extension}"
    container_name = require_blob_container_name()

    try:
        # Upload para Blob Storage
        blob_client = get_blob_service_client().get_blob_client(
            container=container_name, blob=blob_name
        )
        audio_data = audio_file.file.read()
        blob_client.upload_blob(audio_data, overwrite=True)

        # Armazenar em memória para processamento posterior
        _JOBS_METADATA[job_name] = {
            "blob_name": blob_name,
            "status": "PROCESSING",
            "audio_data": audio_data,
            "created_at": time.time(),
        }

    except AzureError as exc:
        raise translate_client_error(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao processar áudio: {str(exc)}") from exc

    return TranscriptionStartResponse(job_name=job_name, status="IN_PROGRESS")


@router.get("/transcription/{job_name}", response_model=TranscriptionResultResponse)
def get_transcription_result(job_name: str) -> TranscriptionResultResponse:
    if job_name not in _JOBS_METADATA:
        raise HTTPException(
            status_code=404, detail=f"Job de transcrição '{job_name}' não encontrado."
        )

    job_metadata = _JOBS_METADATA[job_name]

    if job_metadata.get("status") == "COMPLETED":
        return TranscriptionResultResponse(
            status="COMPLETED",
            transcript=job_metadata.get("transcript", "")
        )

    try:
        audio_data = job_metadata.get("audio_data")
        if not audio_data:
            return TranscriptionResultResponse(status="IN_PROGRESS")

        # Salvar em caminho fixo para evitar problemas com encoding
        tmp_dir = tempfile.gettempdir()
        tmp_path = os.path.join(tmp_dir, f"azure_speech_{job_name}.wav")

        # Escrever arquivo
        with open(tmp_path, "wb") as f:
            f.write(audio_data)

        print(f"[DEBUG] Arquivo: {tmp_path} ({len(audio_data)} bytes)")

        try:
            speech_config = get_speech_config()
            audio_config = AudioConfig(filename=tmp_path)
            recognizer = SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)

            result = recognizer.recognize_once()

            if result.text:
                job_metadata["status"] = "COMPLETED"
                job_metadata["transcript"] = result.text
                _JOBS_METADATA[job_name] = job_metadata
                return TranscriptionResultResponse(status="COMPLETED", transcript=result.text)
            else:
                print(f"[DEBUG] Resultado vazio ou sem reconhecimento: {result}")
                return TranscriptionResultResponse(status="IN_PROGRESS")

        finally:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except:
                    pass

    except AzureError as exc:
        raise translate_client_error(exc) from exc
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erro na transcrição: {str(exc)}") from exc
