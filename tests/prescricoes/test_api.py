"""API da US16 (POST /prescricoes/analise, GET /prescricoes/mimic, GET /prescricoes/config).

Os corpos vêm de ``postman/prescricoes.json``: se a coleção e a regra
divergirem, quebra aqui antes de quebrar no Postman.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import prescricoes as rota
from contratos import BarramentoLocal

COLECAO = Path(__file__).resolve().parents[2] / "postman" / "prescricoes.json"


def _corpos() -> dict[str, dict]:
    colecao = json.loads(COLECAO.read_text(encoding="utf-8"))
    pasta = next(i for i in colecao["item"] if i["name"] == "US16 - Analisar prescrições")
    return {item["name"]: json.loads(item["request"]["body"]["raw"]) for item in pasta["item"]}


CORPOS = _corpos()


def corpo(prefixo: str) -> dict:
    return next(c for nome, c in CORPOS.items() if nome.startswith(prefixo))


@pytest.fixture
def barramento() -> BarramentoLocal:
    return BarramentoLocal()


@pytest.fixture
def publicados(barramento) -> list:
    recebidos: list = []
    barramento.assinar("teste", recebidos.append)
    return recebidos


@pytest.fixture
def client(barramento, mimic):
    app = FastAPI()
    app.include_router(rota.router)
    app.dependency_overrides[rota.get_barramento] = lambda: barramento
    app.dependency_overrides[rota.get_dados_dir] = lambda: mimic
    return TestClient(app)


def _tipos(resposta) -> list[str]:
    return sorted(e["event_type"] for e in resposta.json()["eventos"])


@pytest.mark.parametrize(
    ("prefixo", "tipos"),
    [
        ("Mudança abrupta", ["medicamento_alta_vigilancia", "mudanca_abrupta_dose"]),
        ("Duplicidade", ["duplicidade_terapeutica", "medicamento_alta_vigilancia"]),
        ("Suspensão", ["suspensao_abrupta"]),
        ("Alta vigilância", ["medicamento_alta_vigilancia"]),
        ("Sem achados", ["sem_achados"]),
        ("Unidades", ["mudanca_abrupta_dose"]),
    ],
)
def test_exemplos_da_colecao(client, prefixo, tipos):
    resposta = client.post("/prescricoes/analise?publicar=false", json=corpo(prefixo))
    assert resposta.status_code == 200, resposta.text
    assert _tipos(resposta) == tipos
    dados = resposta.json()
    assert dados["patient_id"].startswith("pt_") and dados["encounter_id"].startswith("enc_")
    assert all(e["modality"] == "prescricoes" and e["is_synthetic"] for e in dados["eventos"])


def test_justificativa_e_antes_depois(client):
    dados = client.post("/prescricoes/analise?publicar=false", json=corpo("Mudança abrupta")).json()
    ev = next(e for e in dados["eventos"] if e["event_type"] == "mudanca_abrupta_dose")
    assert "subiu 400% (40 → 200 mg/dia)" in ev["evidence"]["summary"]
    assert ev["evidence"]["features"]["antes_dose_diaria"] == 40
    assert ev["evidence"]["features"]["depois_dose_diaria"] == 200
    assert ev["severity"] == "media"
    assert dados["por_tipo"] == {"medicamento_alta_vigilancia": 1, "mudanca_abrupta_dose": 1}


def test_detalhes_mostram_unidades_normalizadas(client):
    dados = client.post("/prescricoes/analise?detalhes=true&publicar=false", json=corpo("Unidades")).json()
    assert [(o["dose"], o["unidade"]) for o in dados["normalizadas"]] == [(0.1, "mg"), (0.025, "mg")]
    assert "normalizadas" not in client.post("/prescricoes/analise?publicar=false", json=corpo("Unidades")).json()


def test_publica_no_barramento(client, publicados):
    resposta = client.post("/prescricoes/analise", json=corpo("Suspensão"))
    assert [e.event_type for e in publicados] == ["suspensao_abrupta"]
    assert str(publicados[0].event_id) == resposta.json()["eventos"][0]["event_id"]
    client.post("/prescricoes/analise?publicar=false", json=corpo("Suspensão"))
    assert len(publicados) == 1


def test_pseudonimo_pronto_passa_como_esta(client):
    dados = corpo("Suspensão") | {"patient_id": "pt_" + "a" * 24, "encounter_id": "enc_" + "b" * 24}
    resposta = client.post("/prescricoes/analise?publicar=false", json=dados).json()
    assert resposta["patient_id"] == "pt_" + "a" * 24
    assert resposta["encounter_id"] == "enc_" + "b" * 24


def test_sem_alta_nao_avalia_suspensao(client):
    dados = {k: v for k, v in corpo("Suspensão").items() if k != "alta"}
    assert _tipos(client.post("/prescricoes/analise?publicar=false", json=dados)) == ["sem_achados"]


@pytest.mark.parametrize(
    ("ajuste", "trecho"),
    [
        (lambda c: c, "UTC"),  # corpo do exemplo de erro de fuso
        (lambda c: c | {"prescricoes": []}, "prescricoes"),
        (lambda c: c | {"alta": "2026-09-01T00:00:00Z"}, "alta antes da admissão"),
    ],
)
def test_erros_422(client, ajuste, trecho):
    base = corpo("Erro 422 - horário") if trecho == "UTC" else corpo("Sem achados")
    resposta = client.post("/prescricoes/analise?publicar=false", json=ajuste(base))
    assert resposta.status_code == 422
    assert trecho in resposta.text


def test_fim_antes_do_inicio_e_ordem_repetida_422(client):
    assert "antes do início" in client.post(
        "/prescricoes/analise?publicar=false", json=corpo("Erro 422 - fim")
    ).json()["detail"]
    dados = corpo("Mudança abrupta")
    dados["prescricoes"][1]["ordem_id"] = dados["prescricoes"][0]["ordem_id"]
    resposta = client.post("/prescricoes/analise?publicar=false", json=dados)
    assert resposta.status_code == 422 and "ordem_id repetido" in resposta.json()["detail"]


def test_sem_chave_de_pseudonimizacao_503(client, monkeypatch):
    monkeypatch.delenv("PSEUDONYM_KEY")
    resposta = client.post("/prescricoes/analise?publicar=false", json=corpo("Suspensão"))
    assert resposta.status_code == 503 and "PSEUDONYM_KEY" in resposta.json()["detail"]


# --------------------------------------------------------------------------- #
# MIMIC-IV Demo e config
# --------------------------------------------------------------------------- #


def test_paciente_do_mimic(client):
    rota._mimic.cache_clear()
    resposta = client.get("/prescricoes/mimic/1?publicar=false")
    assert resposta.status_code == 200, resposta.text
    dados = resposta.json()
    assert _tipos(resposta) == ["medicamento_alta_vigilancia", "mudanca_abrupta_dose", "suspensao_abrupta"]
    [internacao] = dados["internacoes"]
    assert internacao["encounter_id"].startswith("enc_") and internacao["total_anomalias"] == 3
    assert internacao["prescricoes_analisadas"] == 3  # a linha duplicada do MIMIC sai
    assert not any(e["is_synthetic"] for e in dados["eventos"])


def test_paciente_do_mimic_sem_anomalia_e_404(client):
    rota._mimic.cache_clear()
    assert _tipos(client.get("/prescricoes/mimic/2?publicar=false")) == ["sem_achados"]
    assert client.get("/prescricoes/mimic/99?publicar=false").status_code == 404
    resposta = client.get("/prescricoes/mimic/1?hadm_id=22&publicar=false")
    assert resposta.status_code == 404 and "não é do paciente" in resposta.json()["detail"]


def test_mimic_nao_baixado_503(client, tmp_path):
    client.app.dependency_overrides[rota.get_dados_dir] = lambda: tmp_path / "vazio"
    resposta = client.get("/prescricoes/mimic/1")
    assert resposta.status_code == 503 and "--baixar" in resposta.json()["detail"]


def test_config_em_uso(client):
    dados = client.get("/prescricoes/config").json()
    assert dados["mudanca_abrupta_dose"]["variacao_min_pct"] == 50
    assert dados["alta_vigilancia"]["classes"]["potassio_concentrado"]["vias"] == ["IV"]
    assert dados["versao_limiares"]


def test_rotas_registradas_no_app():
    from app.main import app

    caminhos = set(app.openapi()["paths"])
    assert {"/prescricoes/analise", "/prescricoes/mimic/{subject_id}", "/prescricoes/config"} <= caminhos
