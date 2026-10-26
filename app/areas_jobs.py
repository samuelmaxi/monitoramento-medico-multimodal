"""Jobs de detecção em áreas críticas (US07) disparados pela API.

Mesmo desenho de ``app.pose_jobs``: o upload só enfileira o job e uma única
thread processa um vídeo por vez, pelo mesmo caminho do terminal
(``video.PipelineAreaCritica``, como em ``scripts/rodar_us07_video.py``).

A configuração (detector, ROIs, monitor) vem de ``config/<camera_id>.json`` ou
de ``US07_CONFIG_PATH``; as ROIs podem ser substituídas por requisição. Os
eventos US04 vão para ``<US07_OUTPUT_DIR>/<job_id>/eventos.jsonl`` e o estado
final para ``job.json``, de onde é relido se a API reiniciar. O vídeo enviado é
apagado ao fim do job.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import shutil
import threading
from dataclasses import replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO, Literal
from uuid import uuid4

from pydantic import BaseModel

from app.pose_jobs import _save_upload
from contratos import Contexto, EmissorJsonl, EventoAchado, agora_utc
from contratos.evento import UtcDatetime
from video import Configuracao, ConjuntoAreas, DetectorYolo, PipelineAreaCritica
from video.config import VERSAO_MODULO, ConfiguracaoDetector

log = logging.getLogger(__name__)

US07_CONFIG_PATH = Path(os.getenv("US07_CONFIG_PATH", "config/exemplo_us07.json"))
US07_CONFIG_DIR = Path(os.getenv("US07_CONFIG_DIR", "config"))
US07_OUTPUT_DIR = Path(os.getenv("US07_OUTPUT_DIR", "saida/video/api"))

_JOB_ID_RE = re.compile(r"^areas-[0-9a-f]{12}$")
CAMERA_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_STATE_FILE = "job.json"
_EVENTS_FILE = "eventos.jsonl"

_jobs: dict[str, AreasJobResponse] = {}
_lock = threading.Lock()
_queue: queue.Queue[tuple[str, Path, Configuracao]] = queue.Queue()
_worker: threading.Thread | None = None


class AreasJobStartResponse(BaseModel):
    job_id: str
    status: str


class AreasSummary(BaseModel):
    """Mesmos campos de ``video.pipeline.ResumoExecucao.para_dict``."""

    quadros_lidos: int
    deteccoes_total: int
    transicoes: int
    eventos: int
    entradas: int
    saidas: int
    fps: float
    resolucao: str
    classes_detectadas: dict[str, int]
    duracao_s: float


class AreasJobResponse(BaseModel):
    """Resultado de um job da US07.

    Os eventos de entrada/saída (``EventoAchado`` da US04) ficam em
    ``GET /video/areas-criticas/{job_id}/eventos``; aqui vão o resumo e os
    parâmetros que o DoD pede documentados (conf, IoU, classes, ROIs).
    """

    job_id: str
    status: Literal["QUEUED", "IN_PROGRESS", "COMPLETED", "FAILED"]
    failure_reason: str | None = None
    patient_id: str | None = None
    context: Contexto | None = None
    ingested_at: UtcDatetime
    model_version: str
    config: dict[str, Any] | None = None
    summary: AreasSummary | None = None


@lru_cache
def get_config(camera_id: str | None = None) -> Configuracao:
    """Configuração da câmera (``config/<camera_id>.json``) ou a padrão."""
    if camera_id is None:
        return Configuracao.de_arquivo(US07_CONFIG_PATH)
    return Configuracao.de_arquivo(US07_CONFIG_DIR / f"{camera_id}.json")


@lru_cache
def get_detector(config: ConfiguracaoDetector) -> DetectorYolo:
    # Um modelo por configuração de detector, reaproveitado entre jobs como no lote da CLI.
    return DetectorYolo(config)


def config_do_job(
    base: Configuracao,
    *,
    source_id: str,
    patient_id: str | None,
    context: Contexto,
    recorded_at: datetime | None,
    areas: ConjuntoAreas | None,
) -> Configuracao:
    """Aplica os campos da requisição sobre a configuração da câmera."""
    fonte = replace(
        base.fonte,
        id=source_id,
        patient_id=patient_id or base.fonte.patient_id,
        encounter_id=context.encounter_id or base.fonte.encounter_id,
        bed_id=context.bed_id or base.fonte.bed_id,
        procedure_type=context.procedure_type or base.fonte.procedure_type,
        # Sem recorded_at o timestamp parte do início da análise, não do instante fixo do exemplo.
        inicio_utc=recorded_at.isoformat().replace("+00:00", "Z") if recorded_at else None,
        max_quadros=None,
    )
    return replace(
        base,
        fonte=fonte,
        areas=areas if areas is not None else base.areas,
        evento=replace(base.evento, emitir_jsonl=False, caminho_jsonl=None),
        visualizacao=replace(base.visualizacao, ativa=False),
    )


def _job_dir(job_id: str) -> Path:
    return US07_OUTPUT_DIR / job_id


def events_path(job_id: str) -> Path:
    return _job_dir(job_id) / _EVENTS_FILE


def submit(src: BinaryIO, extension: str, config: Configuracao) -> AreasJobResponse:
    """Grava o upload em disco e enfileira o job."""
    job_id = f"areas-{uuid4().hex[:12]}"
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True)
    upload = job_dir / f"upload{extension}"
    try:
        _save_upload(src, upload)
    except BaseException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

    fonte = config.fonte
    context = Contexto(
        encounter_id=fonte.encounter_id,
        bed_id=fonte.bed_id,
        source_id=fonte.id,
        procedure_type=fonte.procedure_type,
    )
    job = AreasJobResponse(
        job_id=job_id,
        status="QUEUED",
        patient_id=fonte.patient_id,
        context=context,
        ingested_at=agora_utc(),
        model_version=VERSAO_MODULO,
        # O caminho do vídeo é o do upload temporário, apagado ao fim do job.
        config={k: v for k, v in config.resumo().items() if k != "video"},
    )
    # Já em disco: se a API reiniciar antes do fim, o GET responde FAILED em vez de 404.
    _write_state(job_dir, job)
    with _lock:
        _jobs[job_id] = job
    _queue.put((job_id, upload, replace(config, fonte=replace(fonte, video=upload.resolve()))))
    _ensure_worker()
    return job


def get_job(job_id: str) -> AreasJobResponse | None:
    if not _JOB_ID_RE.fullmatch(job_id):
        return None
    with _lock:
        job = _jobs.get(job_id)
    if job is not None:
        return job
    job_dir = _job_dir(job_id)
    state = job_dir / _STATE_FILE
    if not state.is_file():
        return None
    job = AreasJobResponse.model_validate_json(state.read_text(encoding="utf-8"))
    if job.status in {"QUEUED", "IN_PROGRESS"}:
        # Fora da memória e sem estado final: a API reiniciou (ex.: --reload) com o job na fila.
        for upload in job_dir.glob("upload.*"):
            upload.unlink(missing_ok=True)
        job = job.model_copy(
            update={
                "status": "FAILED",
                "failure_reason": (
                    "Job interrompido: a API reiniciou antes do fim. Envie o vídeo de novo."
                ),
            }
        )
        _write_state(job_dir, job)
    return job


def events(job_id: str) -> list[EventoAchado]:
    """Eventos US04 do job, inclusive o ``sem_achados`` quando não houve transição."""
    linhas = events_path(job_id).read_text(encoding="utf-8").splitlines()
    return [EventoAchado.model_validate_json(linha) for linha in linhas if linha.strip()]


def _ensure_worker() -> None:
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            # daemon: um job longo não segura o encerramento da API.
            _worker = threading.Thread(target=_work, name="areas-worker", daemon=True)
            _worker.start()


def _work() -> None:
    while True:
        job_id, upload, config = _queue.get()
        try:
            _run(job_id, upload, config)
        except Exception:  # a thread precisa sobreviver para os próximos jobs
            log.exception("falha inesperada no job %s", job_id)


def _update(job_id: str, **changes) -> AreasJobResponse:
    with _lock:
        job = _jobs[job_id] = _jobs[job_id].model_copy(update=changes)
    return job


def _run(job_id: str, upload: Path, config: Configuracao) -> None:
    job_dir = upload.parent
    job = _update(job_id, status="IN_PROGRESS")
    try:
        detector = get_detector(config.detector)
        # O ByteTrack guarda tracks no predictor; sem reset, o vídeo anterior vaza para este.
        if hasattr(detector, "reiniciar_rastreamento"):
            detector.reiniciar_rastreamento()
        emissor = EmissorJsonl(events_path(job_id))
        resultado = PipelineAreaCritica(config, detector=detector, emissor=emissor).executar()
        resumo = resultado.resumo.para_dict()
        changes = {
            "status": "COMPLETED",
            "summary": AreasSummary(**{c: resumo[c] for c in AreasSummary.model_fields}),
        }
    except Exception as exc:
        log.exception("job %s falhou", job_id)
        changes = {"status": "FAILED", "failure_reason": f"{type(exc).__name__}: {exc}"}
    finally:
        upload.unlink(missing_ok=True)
    # Grava antes de publicar: quem vê o status final já encontra o job.json em disco.
    _write_state(job_dir, job.model_copy(update=changes))
    _update(job_id, **changes)


def _write_state(job_dir: Path, job: AreasJobResponse) -> None:
    (job_dir / _STATE_FILE).write_text(job.model_dump_json(exclude_none=True), encoding="utf-8")
