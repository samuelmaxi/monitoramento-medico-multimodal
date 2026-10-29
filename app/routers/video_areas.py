import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, UploadFile

from app import areas_jobs, pose_jobs
from app import config as app_config
from app.areas_jobs import AreasJobResponse, AreasJobStartResponse
from app.routers.video_pose import _pseudonym
from contratos import (
    ChavePseudonimizacaoAusente,
    Contexto,
    EventoAchado,
    Janela,
    ValidationError,
    pseudonimizar_internacao,
    pseudonimizar_paciente,
)
from contratos.pseudonimizacao import PADRAO_INTERNACAO, PADRAO_PACIENTE
from video import ConjuntoAreas, areas_de_dados
from video.config import ErroDeConfiguracao
from video.nomes_video import extensao_video_suportada

router = APIRouter(prefix="/video/areas-criticas")


@router.post("", response_model=AreasJobStartResponse, status_code=202)
def start_areas_analysis(
    video_file: UploadFile,
    camera_id: str | None = Form(None),
    areas: str | None = Form(None),
    patient_id: str | None = Form(None),
    encounter_id: str | None = Form(None),
    bed_id: str | None = Form(None),
    source_id: str | None = Form(None),
    procedure_type: str | None = Form(None),
    recorded_at: datetime | None = Form(None),
) -> AreasJobStartResponse:
    """Enfileira a detecção YOLOv8 + ROIs (US07) sobre o vídeo enviado.

    ``camera_id`` escolhe ``config/<camera_id>.json`` (ROIs por câmera); ``areas``
    é uma lista JSON de ROIs no formato de ``config/*.json`` que substitui as da câmera.
    """
    if camera_id is not None and not areas_jobs.CAMERA_ID_RE.fullmatch(camera_id):
        raise HTTPException(
            status_code=400, detail="camera_id aceita só letras, números, '_' e '-'."
        )
    try:
        base = areas_jobs.get_config(camera_id)
    except ErroDeConfiguracao as exc:
        if camera_id is not None:
            raise HTTPException(
                status_code=404, detail=f"Câmera '{camera_id}' sem configuração: {exc}"
            ) from exc
        raise HTTPException(
            status_code=503, detail=f"Detecção de áreas críticas indisponível: {exc}"
        ) from exc

    extension = Path(video_file.filename or "").suffix.lower()
    if not extensao_video_suportada(extension):
        raise HTTPException(
            status_code=400, detail=f"Formato de vídeo não suportado: '{extension}'."
        )

    try:
        rois = ConjuntoAreas(tuple(areas_de_dados(json.loads(areas)))) if areas else None
    except (ValueError, TypeError) as exc:  # JSON, polígono ou campos inválidos
        raise HTTPException(status_code=422, detail=f"areas inválidas: {exc}") from exc
    if rois is not None and not len(rois):
        raise HTTPException(status_code=422, detail="areas precisa ter ao menos uma ROI.")

    try:
        patient = _pseudonym(patient_id, PADRAO_PACIENTE, pseudonimizar_paciente)
        context = Contexto(
            encounter_id=_pseudonym(encounter_id, PADRAO_INTERNACAO, pseudonimizar_internacao),
            bed_id=bed_id,
            procedure_type=procedure_type,
        )
        if recorded_at is not None:
            # O contrato só aceita instantes em UTC.
            recorded_at = Janela(start=recorded_at, end=recorded_at).start
    except ChavePseudonimizacaoAusente as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_url=False, include_context=False, include_input=False),
        ) from exc

    config = areas_jobs.config_do_job(
        base,
        source_id=source_id or camera_id or base.fonte.id,
        patient_id=patient,
        context=context,
        recorded_at=recorded_at,
        areas=rois,
    )
    try:
        job = areas_jobs.submit(video_file.file, extension, config)
    except pose_jobs.UploadTooLarge as exc:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Vídeo maior que o limite de {app_config.VIDEO_MAX_UPLOAD_MB} MB "
                "(VIDEO_MAX_UPLOAD_MB)."
            ),
        ) from exc
    except pose_jobs.EmptyUpload as exc:
        raise HTTPException(status_code=400, detail="O arquivo de vídeo está vazio.") from exc

    return AreasJobStartResponse(job_id=job.job_id, status=job.status)


@router.get("/{job_id}", response_model=AreasJobResponse, response_model_exclude_none=True)
def get_areas_result(job_id: str) -> AreasJobResponse:
    return _require_job(job_id)


@router.get("/{job_id}/eventos", response_model=list[EventoAchado])
def get_areas_events(job_id: str) -> list[EventoAchado]:
    """Eventos US04 ``entrada_area_critica``/``saida_area_critica`` (ou ``sem_achados``)."""
    _require_job(job_id, completed=True)
    return areas_jobs.events(job_id)


def _require_job(job_id: str, completed: bool = False) -> AreasJobResponse:
    job = areas_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=404, detail=f"Job de áreas críticas '{job_id}' não encontrado."
        )
    if completed and job.status != "COMPLETED":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Job de áreas críticas '{job_id}' ainda não tem resultado "
                f"(status: {job.status})."
            ),
        )
    return job
