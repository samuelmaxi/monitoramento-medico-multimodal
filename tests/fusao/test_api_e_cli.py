"""API /fusao e CLI python -m fusao.run."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import fusao as rota
from contratos import EmissorJsonl, EmissorMemoria, Modalidade
from contratos.validar import validar_arquivo
from fusao import FusaoMultimodal
from fusao.cenarios import cenario_dispneia
from fusao.run import main

from .conftest import OUTRO, PACIENTE, T0, achado


@pytest.fixture
def app_fusao(config):
    app = FastAPI()
    app.include_router(rota.router)
    app.state.fusao = FusaoMultimodal(config, EmissorMemoria())
    return app


@pytest.fixture
def client(app_fusao):
    return TestClient(app_fusao)


def _corpo(eventos, **extra):
    return {"eventos": [e.para_dict() for e in eventos], **extra}


class TestApi:
    def test_post_risco_com_explicabilidade(self, client):
        resposta = client.post("/fusao/risco", json=_corpo(cenario_dispneia(PACIENTE, T0)))
        assert resposta.status_code == 200
        [risco] = resposta.json()
        assert risco["nivel"] == "alto"
        assert sum(risco["contribuicoes"].values()) == pytest.approx(risco["score"], abs=1e-3)
        assert risco["modalidades_ausentes"] == ["video", "movimentacao"]
        assert risco["concordancia"]["eixo"] == "respiratorio"
        assert risco["evento"]["modality"] == "fusao"

    def test_post_risco_respeita_janela_e_ate(self, client):
        eventos = cenario_dispneia(PACIENTE, T0)
        resposta = client.post(
            "/fusao/risco",
            json=_corpo(eventos, janela_min=5, ate="2026-09-26T17:00:00Z"),
        )
        [risco] = resposta.json()
        assert risco["janela_inicio"].startswith("2026-09-26T16:55")
        assert "sinais_vitais" not in [m for m, c in risco["contribuicoes"].items() if c > 0]

    def test_post_risco_varios_pacientes(self, client):
        eventos = [achado(Modalidade.AUDIO, "fadiga_vocal"), achado(Modalidade.TEXTO, "termo_critico", paciente=OUTRO)]
        assert {r["patient_id"] for r in client.post("/fusao/risco", json=_corpo(eventos)).json()} == {PACIENTE, OUTRO}

    def test_post_rejeita_evento_fora_do_contrato(self, client):
        corpo = _corpo([achado(Modalidade.AUDIO, "fadiga_vocal")])
        corpo["eventos"][0]["patient_id"] = "12345"
        assert client.post("/fusao/risco", json=corpo).status_code == 422

    def test_get_paciente_reflete_o_barramento(self, client, app_fusao):
        for evento in cenario_dispneia(PACIENTE, T0):
            app_fusao.state.fusao.receber(evento)
        resposta = client.get(f"/fusao/pacientes/{PACIENTE}")
        assert resposta.status_code == 200 and resposta.json()["nivel"] == "alto"
        assert [r["patient_id"] for r in client.get("/fusao/pacientes").json()] == [PACIENTE]

    def test_get_paciente_desconhecido(self, client):
        assert client.get(f"/fusao/pacientes/{OUTRO}").status_code == 404

    def test_sem_servico_iniciado(self):
        app = FastAPI()
        app.include_router(rota.router)
        assert TestClient(app).get("/fusao/pacientes").status_code == 503


class TestCli:
    def test_le_jsonl_e_grava_riscos(self, tmp_path, capsys):
        entrada, saida = tmp_path / "eventos.jsonl", tmp_path / "fusao.jsonl"
        EmissorJsonl(entrada).emitir_varios(cenario_dispneia(PACIENTE, T0))

        assert main([str(entrada), "--saida", str(saida)]) == 0

        assert PACIENTE in capsys.readouterr().out
        [risco], erros = validar_arquivo(saida)
        assert not erros and risco.modality is Modalidade.FUSAO and risco.severity.value == "alta"

    def test_evento_invalido_sai_com_1(self, tmp_path):
        entrada = tmp_path / "eventos.jsonl"
        entrada.write_text('{"nao": "e evento"}\n', encoding="utf-8")
        assert main([str(entrada)]) == 1
