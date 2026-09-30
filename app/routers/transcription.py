import json
from uuid import uuid4

from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, UploadFile

from app.aws_clients import get_s3_client, get_transcribe_client
from app.config import require_s3_bucket_name
from app.errors import translate_client_error
from app.schemas import TranscriptionResultResponse, TranscriptionStartResponse

router = APIRouter()

_SUPPORTED_FORMATS = {"wav", "mp3", "mp4", "flac", "ogg", "amr", "webm"}
_INPUT_PREFIX = "transcription-input"
_OUTPUT_PREFIX = "transcription-output"


@router.post("/transcription", response_model=TranscriptionStartResponse)
def start_transcription(audio_file: UploadFile) -> TranscriptionStartResponse:
    extension = (audio_file.filename or "").rsplit(".", 1)[-1].lower()
    if extension not in _SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Formato de áudio não suportado pelo Amazon Transcribe: "
                f"'{extension}'. Formatos aceitos: {sorted(_SUPPORTED_FORMATS)}."
            ),
        )

    bucket = require_s3_bucket_name()
    job_name = f"smoke-test-{uuid4().hex[:12]}"
    input_key = f"{_INPUT_PREFIX}/{job_name}.{extension}"
    output_key = f"{_OUTPUT_PREFIX}/{job_name}.json"

    try:
        get_s3_client().upload_fileobj(audio_file.file, bucket, input_key)
        get_transcribe_client().start_transcription_job(
            TranscriptionJobName=job_name,
            LanguageCode="pt-BR",
            MediaFormat=extension,
            Media={"MediaFileUri": f"s3://{bucket}/{input_key}"},
            OutputBucketName=bucket,
            OutputKey=output_key,
        )
    except ClientError as exc:
        raise translate_client_error(exc) from exc

    return TranscriptionStartResponse(job_name=job_name, status="IN_PROGRESS")


@router.get("/transcription/{job_name}", response_model=TranscriptionResultResponse)
def get_transcription_result(job_name: str) -> TranscriptionResultResponse:
    try:
        job = get_transcribe_client().get_transcription_job(
            TranscriptionJobName=job_name
        )["TranscriptionJob"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "BadRequestException":
            raise HTTPException(
                status_code=404, detail=f"Job de transcrição '{job_name}' não encontrado."
            ) from exc
        raise translate_client_error(exc) from exc

    status = job["TranscriptionJobStatus"]

    if status == "FAILED":
        return TranscriptionResultResponse(
            status=status, failure_reason=job.get("FailureReason")
        )

    if status != "COMPLETED":
        return TranscriptionResultResponse(status=status)

    bucket = require_s3_bucket_name()
    output_key = f"{_OUTPUT_PREFIX}/{job_name}.json"
    try:
        obj = get_s3_client().get_object(Bucket=bucket, Key=output_key)
        transcript_json = json.loads(obj["Body"].read())
    except ClientError as exc:
        raise translate_client_error(exc) from exc

    transcript = transcript_json["results"]["transcripts"][0]["transcript"]
    return TranscriptionResultResponse(status=status, transcript=transcript)
