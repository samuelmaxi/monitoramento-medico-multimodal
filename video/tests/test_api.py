"""Testes da API de pose (POST/GET /video/pose) com um estimador falso, sem os pesos do OpenPose."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config as app_config
from app import pose_jobs
from app.routers import video_pose
from contratos import Artefato, Contexto, EventoAchado, Modalidade, Severidade, pseudonimizar_paciente
from video.pose.config import load_config
from video.pose.models import BODY_25
from video.pose.sequence import PoseSequence

CONFIG_PATH = Path(__file__).resolve().parents[1] / "pose" / "config.yaml"
CHAVE = "k" * 32


class EstimadorFalso:
    """Devolve sempre a mesma pessoa em pé, com todos os keypoints confiantes."""

    keypoint_names = BODY_25.keypoints
    backend = "cpu"

    def estimate(self, frame):
        return [(10.0 + i, 5.0 + 2 * i, 0.9) for i in range(len(self.keypoint_names))]


def _video_bytes(tmp_path: Path, fps: float = 30, n_frames: int = 30) -> bytes:
    path = tmp_path / "origem.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (64, 48))
    assert writer.isOpened()
    for i in range(n_frames):
        writer.write(np.full((48, 64, 3), i % 256, np.uint8))
    writer.release()
    return path.read_bytes()


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Saída relativa ao diretório corrente, como no repositório (o contrato exige URI relativa).
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PSEUDONYM_KEY", CHAVE)
    cfg = load_config(CONFIG_PATH)
    cfg = replace(cfg, output=replace(cfg.output, dir=Path("outputs/pose")))
    monkeypatch.setattr(pose_jobs, "get_pose_config", lambda: cfg)
    monkeypatch.setattr(pose_jobs, "get_estimator", lambda: EstimadorFalso())
    monkeypatch.setattr(pose_jobs, "_jobs", {})
    app = FastAPI()
    app.include_router(video_pose.router)
    return TestClient(app)


def _enviar(client, conteudo: bytes, nome: str = "sessao.avi", **campos):
    return client.post("/video/pose", files={"video_file": (nome, conteudo, "video/x-msvideo")}, data=campos)


def _aguardar(client, job_id: str) -> dict:
    limite = time.monotonic() + 30
    while time.monotonic() < limite:
        resposta = client.get(f"/video/pose/{job_id}")
        assert resposta.status_code == 200
        corpo = resposta.json()
        if corpo["status"] in {"COMPLETED", "FAILED"}:
            return corpo
        time.sleep(0.05)
    pytest.fail(f"job {job_id} não terminou")


class TestFluxoCompleto:
    def test_upload_processa_e_devolve_resultado(self, client, tmp_path):
        inicio = _enviar(
            client, _video_bytes(tmp_path),
            patient_id="10000032", encounter_id="22595853", bed_id="UTI-07",
            source_id="fisio-003", procedure_type="fisioterapia",
            recorded_at="2026-09-26T15:20:00Z",
        )
        assert inicio.status_code == 202, inicio.text
        job_id = inicio.json()["job_id"]

        corpo = _aguardar(client, job_id)
        assert corpo["status"] == "COMPLETED", corpo.get("failure_reason")
        assert corpo["summary"]["frames_processados"] == 10
        assert corpo["summary"]["pct_frames_com_esqueleto"] == 100.0
        assert corpo["summary"]["modelo"] == "BODY_25"
        assert corpo["angles"]["joelho_direito"]["quadros_validos"] == 10
        assert corpo["patient_id"] == pseudonimizar_paciente("10000032", chave=CHAVE)
        assert corpo["context"]["encounter_id"].startswith("enc_")
        assert corpo["window"] == {"start": "2026-09-26T15:20:00.000Z", "end": "2026-09-26T15:20:01.000Z"}
        assert [a["uri"] for a in corpo["artifacts"]] == [
            f"outputs/pose/api/{job_id}/{job_id}.json",
            f"outputs/pose/api/{job_id}/{job_id}_angles.csv",
        ]

    def test_resultado_monta_evento_do_contrato(self, client, tmp_path):
        job_id = _enviar(
            client, _video_bytes(tmp_path), patient_id="10000032", source_id="fisio-003",
            recorded_at="2026-09-26T15:20:00Z",
        ).json()["job_id"]
        corpo = _aguardar(client, job_id)

        evento = EventoAchado.criar(
            patient_id=corpo["patient_id"],
            modality=Modalidade.VIDEO,
            event_type="postura_fora_da_faixa",
            timestamp=corpo["window"]["start"],
            window=corpo["window"],
            ingested_at=corpo["ingested_at"],
            score=0.66,
            severity=Severidade.MEDIA,
            evidence={"summary": "Flexão de joelho acima da faixa", "artifacts": corpo["artifacts"]},
            model_version=corpo["model_version"],
            context=corpo["context"],
        )
        assert evento.context == Contexto(source_id="fisio-003")
        assert all(isinstance(a, Artefato) for a in evento.evidence.artifacts)

    def test_downloads_e_limpeza_do_upload(self, client, tmp_path):
        job_id = _enviar(client, _video_bytes(tmp_path)).json()["job_id"]
        corpo = _aguardar(client, job_id)
        assert corpo["status"] == "COMPLETED"
        assert "patient_id" not in corpo and "context" not in corpo and "window" not in corpo

        keypoints = client.get(f"/video/pose/{job_id}/keypoints")
        assert keypoints.status_code == 200
        seq = PoseSequence.from_dict(keypoints.json())
        assert seq.video_id == job_id and len(seq.frames) == 10

        angulos = client.get(f"/video/pose/{job_id}/angles")
        assert angulos.status_code == 200
        assert angulos.text.splitlines()[0].startswith("frame_idx,timestamp_ms,joelho_esquerdo")

        job_dir = tmp_path / "outputs" / "pose" / "api" / job_id
        assert {p.name for p in job_dir.iterdir()} == {f"{job_id}.json", f"{job_id}_angles.csv", "job.json"}

    def test_feedback_em_linguagem_natural(self, client, tmp_path):
        job_id = _enviar(client, _video_bytes(tmp_path)).json()["job_id"]
        _aguardar(client, job_id)

        resposta = client.get(f"/video/pose/{job_id}/feedback")
        assert resposta.status_code == 200, resposta.text
        corpo = resposta.json()
        assert set(corpo) == {"job_id", "resumo", "pontos_positivos", "pontos_negativos", "pontos_de_alerta", "aviso"}
        assert corpo["job_id"] == job_id
        assert corpo["resumo"] and corpo["aviso"]
        assert any("100,0% dos quadros" in p for p in corpo["pontos_positivos"])

    def test_job_concluido_sobrevive_a_reinicio(self, client, tmp_path, monkeypatch):
        job_id = _enviar(client, _video_bytes(tmp_path)).json()["job_id"]
        antes = _aguardar(client, job_id)
        monkeypatch.setattr(pose_jobs, "_jobs", {})
        assert client.get(f"/video/pose/{job_id}").json() == antes


class TestErros:
    def test_video_corrompido_vira_failed(self, client):
        job_id = _enviar(client, b"isto nao e um video" * 100, nome="ruim.mp4").json()["job_id"]
        corpo = _aguardar(client, job_id)
        assert corpo["status"] == "FAILED"
        assert "VideoReadError" in corpo["failure_reason"]
        assert client.get(f"/video/pose/{job_id}/keypoints").status_code == 409
        assert client.get(f"/video/pose/{job_id}/feedback").status_code == 409

    def test_extensao_nao_suportada(self, client):
        assert _enviar(client, b"x", nome="audio.wav").status_code == 400

    def test_arquivo_vazio(self, client, tmp_path):
        assert _enviar(client, b"").status_code == 400
        assert not list((tmp_path / "outputs" / "pose" / "api").iterdir())

    def test_acima_do_limite(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(app_config, "VIDEO_MAX_UPLOAD_MB", 0)
        assert _enviar(client, _video_bytes(tmp_path)).status_code == 413

    def test_contexto_fora_do_contrato(self, client, tmp_path):
        resposta = _enviar(client, _video_bytes(tmp_path), bed_id="leito 7 / ala norte")
        assert resposta.status_code == 422

    def test_recorded_at_sem_fuso(self, client, tmp_path):
        resposta = _enviar(client, _video_bytes(tmp_path), recorded_at="2026-09-26T15:20:00")
        assert resposta.status_code == 422

    def test_id_cru_sem_chave_de_pseudonimizacao(self, client, tmp_path, monkeypatch):
        monkeypatch.delenv("PSEUDONYM_KEY")
        resposta = _enviar(client, _video_bytes(tmp_path), patient_id="10000032")
        assert resposta.status_code == 503
        assert "PSEUDONYM_KEY" in resposta.json()["detail"]

    def test_modelo_ausente(self, client, tmp_path, monkeypatch):
        def sem_pesos():
            raise FileNotFoundError("arquivo do modelo não encontrado")

        monkeypatch.setattr(pose_jobs, "get_estimator", sem_pesos)
        assert _enviar(client, _video_bytes(tmp_path)).status_code == 503

    def test_job_inexistente(self, client):
        assert client.get("/video/pose/pose-000000000000").status_code == 404
        assert client.get("/video/pose/qualquer-coisa").status_code == 404
