import json
import time
from uuid import uuid4

from azure.core.exceptions import AzureError, ResourceNotFoundError
from fastapi import APIRouter, HTTPException, UploadFile

from app.azure_clients import get_blob_service_client
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
        blob_client = get_blob_service_client().get_blob_client(
            container=container_name, blob=blob_name
        )
        blob_client.upload_blob(audio_file.file, overwrite=True)

        _JOBS_METADATA[job_name] = {
            "blob_name": blob_name,
            "status": "IN_PROGRESS",
            "created_at": time.time(),
        }
    except AzureError as exc:
        raise translate_client_error(exc) from exc

    return TranscriptionStartResponse(job_name=job_name, status="IN_PROGRESS")


@router.get("/transcription/{job_name}", response_model=TranscriptionResultResponse)
def get_transcription_result(job_name: str) -> TranscriptionResultResponse:
    if job_name not in _JOBS_METADATA:
        raise HTTPException(
            status_code=404, detail=f"Job de transcrição '{job_name}' não encontrado."
        )

    job_metadata = _JOBS_METADATA[job_name]
    container_name = require_blob_container_name()
    output_blob_name = f"{_OUTPUT_PREFIX}/{job_name}.json"

    try:
        blob_service_client = get_blob_service_client()
        blob_client = blob_service_client.get_blob_client(
            container=container_name, blob=output_blob_name
        )

        try:
            transcript_data = blob_client.download_blob().readall()
            transcript_json = json.loads(transcript_data)

            job_metadata["status"] = "COMPLETED"
            _JOBS_METADATA[job_name] = job_metadata

            transcript = transcript_json.get("results", [{}])[0].get(
                "transcripts", [{}]
            )[0].get("transcript", "")

            return TranscriptionResultResponse(status="COMPLETED", transcript=transcript)
        except ResourceNotFoundError:
            return TranscriptionResultResponse(status="IN_PROGRESS")
    except AzureError as exc:
        if "NotFound" in str(exc):
            return TranscriptionResultResponse(status="IN_PROGRESS")
        raise translate_client_error(exc) from exc
