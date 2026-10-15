"""Jobs de análise de pose (US06) disparados pela API.

A extração leva minutos por vídeo em CPU, então o upload só enfileira o job.
Uma única thread processa um vídeo por vez, reaproveitando o modelo carregado,
pelo mesmo caminho do terminal (``video.pose.run.process_video``).

O estado fica em memória e, ao terminar, em ``<output.dir>/api/<job_id>/job.json``,
de onde é relido se a API reiniciar. O vídeo enviado é apagado ao fim do job.
"""

from __future__ import annotations

import logging
import queue
import re
import shutil
import threading
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

import pandas as pd

from app import config as app_config
from app.schemas import PoseAngleStats, PoseFeedbackResponse, PoseJobResponse, PoseSummary
from contratos import Artefato, Contexto, Janela, agora_utc
from video.pose import __version__ as POSE_VERSION
from video.pose.angles import ANGLE_COLUMNS
from video.pose.config import PoseConfig, load_config
from video.pose.feedback import build_feedback
from video.pose.openpose import OpenPoseEstimator
from video.pose.run import process_video

log = logging.getLogger(__name__)

MODEL_VERSION = f"video-pose@{POSE_VERSION}"

_JOB_ID_RE = re.compile(r"^pose-[0-9a-f]{12}$")
_STATE_FILE = "job.json"
_CHUNK_BYTES = 1024 * 1024

_jobs: dict[str, PoseJobResponse] = {}
_lock = threading.Lock()
_queue: queue.Queue[tuple[str, Path, datetime | None]] = queue.Queue()
_worker: threading.Thread | None = None


class UploadTooLarge(Exception):
    """O vídeo enviado passa de ``VIDEO_MAX_UPLOAD_MB``."""


class EmptyUpload(Exception):
    """O arquivo enviado não tem conteúdo."""


@lru_cache
def get_pose_config() -> PoseConfig:
    return load_config(app_config.POSE_CONFIG_PATH)


@lru_cache
def get_estimator() -> OpenPoseEstimator:
    cfg = get_pose_config()
    return OpenPoseEstimator(cfg.model, cfg.postprocess)


def _job_dir(job_id: str) -> Path:
    return get_pose_config().output.dir / "api" / job_id


def keypoints_path(job_id: str) -> Path:
    return _job_dir(job_id) / f"{job_id}.json"


def angles_path(job_id: str) -> Path:
    return _job_dir(job_id) / f"{job_id}_angles.csv"


def submit(
    src: BinaryIO,
    extension: str,
    *,
    patient_id: str | None,
    context: Contexto | None,
    recorded_at: datetime | None,
) -> PoseJobResponse:
    """Grava o upload em disco e enfileira o job."""
    job_id = f"pose-{uuid4().hex[:12]}"
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True)
    upload = job_dir / f"upload{extension}"
    try:
        _save_upload(src, upload)
    except BaseException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

    job = PoseJobResponse(
        job_id=job_id,
        status="QUEUED",
        patient_id=patient_id,
        context=context,
        ingested_at=agora_utc(),
        model_version=MODEL_VERSION,
    )
    with _lock:
        _jobs[job_id] = job
    _queue.put((job_id, upload, recorded_at))
    _ensure_worker()
    return job


def get_job(job_id: str) -> PoseJobResponse | None:
    if not _JOB_ID_RE.fullmatch(job_id):
        return None
    with _lock:
        job = _jobs.get(job_id)
    if job is not None:
        return job
    state = _job_dir(job_id) / _STATE_FILE
    if state.is_file():
        return PoseJobResponse.model_validate_json(state.read_text(encoding="utf-8"))
    return None


def feedback(job: PoseJobResponse) -> PoseFeedbackResponse:
    """Feedback em linguagem natural de um job ``COMPLETED``, calculado a partir do CSV de ângulos."""
    resultado = build_feedback(
        pd.read_csv(angles_path(job.job_id)),
        frames_com_esqueleto=job.summary.frames_com_esqueleto,
        pct_frames_com_esqueleto=job.summary.pct_frames_com_esqueleto,
        fps_processado=job.summary.fps_processado,
        duracao_s=job.summary.duracao_s,
        cfg=get_pose_config().feedback,
    )
    return PoseFeedbackResponse(job_id=job.job_id, **asdict(resultado))


def _save_upload(src: BinaryIO, dest: Path) -> None:
    limit = app_config.VIDEO_MAX_UPLOAD_MB * 1024 * 1024
    size = 0
    with dest.open("wb") as out:
        while chunk := src.read(_CHUNK_BYTES):
            size += len(chunk)
            if size > limit:
                raise UploadTooLarge
            out.write(chunk)
    if size == 0:
        raise EmptyUpload


def _ensure_worker() -> None:
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            # daemon: um job longo não segura o encerramento da API.
            _worker = threading.Thread(target=_work, name="pose-worker", daemon=True)
            _worker.start()


def _work() -> None:
    while True:
        job_id, upload, recorded_at = _queue.get()
        try:
            _run(job_id, upload, recorded_at)
        except Exception:  # a thread precisa sobreviver para os próximos jobs
            log.exception("falha inesperada no job %s", job_id)


def _update(job_id: str, **changes) -> PoseJobResponse:
    with _lock:
        job = _jobs[job_id] = _jobs[job_id].model_copy(update=changes)
    return job


def _run(job_id: str, upload: Path, recorded_at: datetime | None) -> None:
    job_dir = upload.parent
    job = _update(job_id, status="IN_PROGRESS")
    try:
        cfg = get_pose_config()
        cfg = replace(cfg, output=replace(cfg.output, dir=job_dir))
        row = process_video(upload, job_id, cfg, get_estimator())
        window = None
        if recorded_at is not None:
            window = Janela(start=recorded_at, end=recorded_at + timedelta(seconds=row["duracao_s"]))
        changes = dict(
            status="COMPLETED",
            window=window,
            summary=PoseSummary(**{campo: row[campo] for campo in PoseSummary.model_fields}),
            angles=_angle_stats(pd.read_csv(angles_path(job_id))),
            artifacts=_artifacts(job_id),
        )
    except Exception as exc:
        log.exception("job %s falhou", job_id)
        changes = dict(status="FAILED", failure_reason=f"{type(exc).__name__}: {exc}")
    finally:
        upload.unlink(missing_ok=True)
    # Grava antes de publicar: quem vê o status final já encontra o job.json em disco.
    final = job.model_copy(update=changes)
    (job_dir / _STATE_FILE).write_text(final.model_dump_json(exclude_none=True), encoding="utf-8")
    _update(job_id, **changes)


def _angle_stats(angles: pd.DataFrame) -> dict[str, PoseAngleStats]:
    stats: dict[str, PoseAngleStats] = {}
    for coluna in ANGLE_COLUMNS:
        validos = angles[coluna].dropna()
        if validos.empty:
            stats[coluna] = PoseAngleStats(quadros_validos=0)
            continue
        stats[coluna] = PoseAngleStats(
            quadros_validos=len(validos),
            min=round(float(validos.min()), 2),
            max=round(float(validos.max()), 2),
            media=round(float(validos.mean()), 2),
        )
    return stats


def _artifacts(job_id: str) -> list[Artefato]:
    """Saídas como ``Artefato`` do contrato, que só aceita caminho relativo à raiz do repositório."""
    try:
        raiz = Path.cwd().resolve()
        return [
            Artefato(kind=kind, uri=path.resolve().relative_to(raiz).as_posix(), media_type=media_type)
            for kind, path, media_type in (
                ("outro", keypoints_path(job_id), "application/json"),
                ("serie_temporal", angles_path(job_id), "text/csv"),
            )
        ]
    except ValueError:
        log.warning("job %s: saída fora da raiz do repositório; artifacts ficará vazio", job_id)
        return []
