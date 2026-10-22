"""API da US07 (POST/GET /video/areas-criticas) com detector falso, sem pesos do YOLO."""

from __future__ import annotations

import json
import queue
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import areas_jobs
from app.routers import video_areas
from contratos import EventoAchado
from video import Caixa, DetectorFalso, criar_video_teste

from .conftest import PACIENTE, deteccao

GET_CONFIG = areas_jobs.get_config  # a real, antes do monkeypatch do client

# Fora → dentro → dentro → fora da ROI "teste" (0..100 px), como no teste de integração.
QUADROS_COM_TRANSICAO = [
    [deteccao(Caixa(200, 100, 240, 180), frame_index=0, tempo_s=0.0)],
    [deteccao(Caixa(20, 30, 90, 90), frame_index=1, tempo_s=0.1)],
    [deteccao(Caixa(20, 30, 90, 90), frame_index=2, tempo_s=0.2)],
    [deteccao(Caixa(200, 100, 240, 180), frame_index=3, tempo_s=0.3)],
]


@pytest.fixture
def detector():
    return DetectorFalso(QUADROS_COM_TRANSICAO)


@pytest.fixture
def client(tmp_path, monkeypatch, config_base, detector):
    monkeypatch.setattr(areas_jobs, "US07_OUTPUT_DIR", tmp_path / "api")
    monkeypatch.setattr(areas_jobs, "US07_CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(areas_jobs, "get_config", lambda camera_id=None: config_base)
    monkeypatch.setattr(areas_jobs, "get_detector", lambda _config: detector)
    monkeypatch.setattr(areas_jobs, "_jobs", {})
    app = FastAPI()
    app.include_router(video_areas.router)
    return TestClient(app)


@pytest.fixture
def video_bytes(tmp_path) -> bytes:
    return criar_video_teste(tmp_path / "upload.avi", quadros=4, fps=10.0).read_bytes()


def _enviar(client, conteudo: bytes, nome: str = "sessao.avi", **campos):
    return client.post(
        "/video/areas-criticas",
        files={"video_file": (nome, conteudo, "video/x-msvideo")},
        data=campos,
    )


def _aguardar(client, job_id: str) -> dict:
    limite = time.monotonic() + 30
    while time.monotonic() < limite:
        resposta = client.get(f"/video/areas-criticas/{job_id}")
        assert resposta.status_code == 200
        corpo = resposta.json()
        if corpo["status"] in {"COMPLETED", "FAILED"}:
            return corpo
        time.sleep(0.05)
    pytest.fail(f"job {job_id} não terminou")


def test_upload_emite_entrada_e_saida_no_contrato_us04(client, video_bytes):
    inicio = _enviar(
        client,
        video_bytes,
        patient_id="10000032",
        bed_id="UTI-09",
        source_id="cam-api",
        recorded_at="2026-10-05T19:00:00Z",
    )
    assert inicio.status_code == 202
    job_id = inicio.json()["job_id"]

    job = _aguardar(client, job_id)
    assert job["status"] == "COMPLETED", job.get("failure_reason")
    assert job["patient_id"] == PACIENTE  # ID cru pseudonimizado com a chave do teste
    assert job["context"] == {"bed_id": "UTI-09", "source_id": "cam-api", "procedure_type": "fisioterapia"}
    assert job["config"]["confianca"] == 0.25
    assert job["config"]["iou"] == 0.5
    assert "video" not in job["config"]
    assert job["summary"]["entradas"] == 1
    assert job["summary"]["saidas"] == 1

    resposta = client.get(f"/video/areas-criticas/{job_id}/eventos")
    assert resposta.status_code == 200
    eventos = [EventoAchado.model_validate(e) for e in resposta.json()]
    assert [e.event_type for e in eventos] == ["entrada_area_critica", "saida_area_critica"]
    # recorded_at + tempo do quadro
    assert eventos[0].timestamp.isoformat() == "2026-10-05T19:00:00.100000+00:00"
    assert eventos[0].evidence.features["area_critica_id"] == "teste"
    assert all(e.patient_id == PACIENTE and e.context.bed_id == "UTI-09" for e in eventos)


def test_areas_da_requisicao_substituem_as_da_camera(client, video_bytes):
    longe = [{"id": "longe", "nome": "Longe", "points": [[300, 200], [319, 200], [319, 239], [300, 239]]}]
    job_id = _enviar(client, video_bytes, areas=json.dumps(longe)).json()["job_id"]

    job = _aguardar(client, job_id)
    assert job["config"]["areas"] == ["longe"]
    eventos = client.get(f"/video/areas-criticas/{job_id}/eventos").json()
    assert [e["event_type"] for e in eventos] == ["sem_achados"]


@pytest.mark.parametrize(
    ("campos", "nome", "status"),
    [
        ({}, "sessao.txt", 400),
        ({"camera_id": "../segredo"}, "sessao.avi", 400),
        ({"areas": "nao-e-json"}, "sessao.avi", 422),
        ({"areas": "[]"}, "sessao.avi", 422),
        ({"areas": '[{"id": "x", "points": [[0, 0], [1, 1]]}]'}, "sessao.avi", 422),
    ],
)
def test_requisicao_invalida_nao_cria_job(client, video_bytes, campos, nome, status):
    assert _enviar(client, video_bytes, nome=nome, **campos).status_code == status
    assert areas_jobs._jobs == {}


def test_camera_sem_configuracao_responde_404(client, video_bytes, monkeypatch):
    monkeypatch.setattr(areas_jobs, "get_config", GET_CONFIG)
    assert _enviar(client, video_bytes, camera_id="camera_inexistente").status_code == 404


def test_upload_vazio_responde_400(client):
    assert _enviar(client, b"").status_code == 400


def test_recorded_at_fora_de_utc_responde_422(client, video_bytes):
    assert _enviar(client, video_bytes, recorded_at="2026-10-05T16:00:00-03:00").status_code == 422


def test_job_inexistente_responde_404(client):
    assert client.get("/video/areas-criticas/areas-000000000000").status_code == 404
    assert client.get("/video/areas-criticas/../etc").status_code == 404


def test_job_relido_do_disco_apos_reinicio(client, video_bytes, monkeypatch):
    job_id = _enviar(client, video_bytes).json()["job_id"]
    _aguardar(client, job_id)
    monkeypatch.setattr(areas_jobs, "_jobs", {})  # simula a API reiniciada

    assert client.get(f"/video/areas-criticas/{job_id}").json()["status"] == "COMPLETED"
    assert client.get(f"/video/areas-criticas/{job_id}/eventos").status_code == 200


def test_job_interrompido_por_reinicio_vira_failed(client, video_bytes, monkeypatch):
    # Fila própria e sem worker: o job fica parado, sem a thread de outro teste pegá-lo.
    monkeypatch.setattr(areas_jobs, "_queue", queue.Queue())
    monkeypatch.setattr(areas_jobs, "_ensure_worker", lambda: None)
    job_id = _enviar(client, video_bytes).json()["job_id"]
    monkeypatch.setattr(areas_jobs, "_jobs", {})  # simula a API reiniciada (ex.: --reload)

    job = client.get(f"/video/areas-criticas/{job_id}").json()
    assert job["status"] == "FAILED"
    assert "reiniciou" in job["failure_reason"]
    assert not list((areas_jobs.US07_OUTPUT_DIR / job_id).glob("upload.*"))
    assert client.get(f"/video/areas-criticas/{job_id}/eventos").status_code == 409
