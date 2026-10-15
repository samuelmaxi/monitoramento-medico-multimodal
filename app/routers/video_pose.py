import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app import config as app_config
from app import pose_jobs
from app.schemas import PoseFeedbackResponse, PoseJobResponse, PoseJobStartResponse
from contratos import (
    ChavePseudonimizacaoAusente,
    Contexto,
    Janela,
    ValidationError,
    pseudonimizar_internacao,
    pseudonimizar_paciente,
)
from contratos.pseudonimizacao import PADRAO_INTERNACAO, PADRAO_PACIENTE

router = APIRouter(prefix="/video/pose")


@router.post("", response_model=PoseJobStartResponse, status_code=202)
def start_pose_analysis(
    video_file: UploadFile,
    patient_id: str | None = Form(None),
    encounter_id: str | None = Form(None),
    bed_id: str | None = Form(None),
    source_id: str | None = Form(None),
    procedure_type: str | None = Form(None),
    recorded_at: datetime | None = Form(None),
) -> PoseJobStartResponse:
    try:
        config = pose_jobs.get_pose_config()
        pose_jobs.get_estimator()
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail=f"Análise de pose indisponível: {exc}"
        ) from exc

    extension = Path(video_file.filename or "").suffix.lower()
    if extension not in config.input.extensions:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Formato de vídeo não suportado: '{extension}'. "
                f"Formatos aceitos: {sorted(config.input.extensions)}."
            ),
        )

    try:
        patient = _pseudonym(patient_id, PADRAO_PACIENTE, pseudonimizar_paciente)
        context = Contexto(
            encounter_id=_pseudonym(encounter_id, PADRAO_INTERNACAO, pseudonimizar_internacao),
            bed_id=bed_id,
            source_id=source_id,
            procedure_type=procedure_type,
        )
        if recorded_at is not None:
            # O contrato só aceita instantes em UTC; a janela final sai com a duração do vídeo.
            recorded_at = Janela(start=recorded_at, end=recorded_at).start
    except ChavePseudonimizacaoAusente as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_url=False, include_context=False, include_input=False),
        ) from exc

    try:
        job = pose_jobs.submit(
            video_file.file,
            extension,
            patient_id=patient,
            context=context if context != Contexto() else None,
            recorded_at=recorded_at,
        )
    except pose_jobs.UploadTooLarge as exc:
        raise HTTPException(
            status_code=413,
            detail=f"Vídeo maior que o limite de {app_config.VIDEO_MAX_UPLOAD_MB} MB (VIDEO_MAX_UPLOAD_MB).",
        ) from exc
    except pose_jobs.EmptyUpload as exc:
        raise HTTPException(status_code=400, detail="O arquivo de vídeo está vazio.") from exc

    return PoseJobStartResponse(job_id=job.job_id, status=job.status)


@router.get("/{job_id}", response_model=PoseJobResponse, response_model_exclude_none=True)
def get_pose_result(job_id: str) -> PoseJobResponse:
    return _require_job(job_id)


@router.get("/{job_id}/feedback", response_model=PoseFeedbackResponse)
def get_pose_feedback(job_id: str) -> PoseFeedbackResponse:
    return pose_jobs.feedback(_require_job(job_id, completed=True))


@router.get("/{job_id}/keypoints")
def download_keypoints(job_id: str) -> FileResponse:
    _require_job(job_id, completed=True)
    return FileResponse(
        pose_jobs.keypoints_path(job_id), media_type="application/json", filename=f"{job_id}.json"
    )


@router.get("/{job_id}/angles")
def download_angles(job_id: str) -> FileResponse:
    _require_job(job_id, completed=True)
    return FileResponse(
        pose_jobs.angles_path(job_id), media_type="text/csv", filename=f"{job_id}_angles.csv"
    )


def _require_job(job_id: str, completed: bool = False) -> PoseJobResponse:
    job = pose_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job de pose '{job_id}' não encontrado.")
    if completed and job.status != "COMPLETED":
        raise HTTPException(
            status_code=409,
            detail=f"Job de pose '{job_id}' ainda não tem resultado (status: {job.status}).",
        )
    return job


def _pseudonym(value: str | None, pattern: str, pseudonymize: Callable[[str], str]) -> str | None:
    """Aceita o pseudônimo pronto ou o ID de origem, que é pseudonimizado antes de qualquer uso."""
    if value is None or re.fullmatch(pattern, value):
        return value
    return pseudonymize(value)
