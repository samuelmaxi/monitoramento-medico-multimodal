"""Roda a coleção ``postman/fusao.json`` contra o app de verdade (com lifespan).

Os corpos e as URLs vêm da coleção gerada por ``scripts/gerar_postman_us18.py``;
as verificações abaixo são as mesmas dos ``pm.test``. Se a fusão mudar e um
exemplo deixar de dar o resultado esperado, este teste quebra antes do Postman.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from .conftest import RAIZ

COLECAO = RAIZ / "postman" / "fusao.json"


def _requisicoes() -> list[dict]:
    """Requisições na ordem da coleção (pastas achatadas)."""
    colecao = json.loads(COLECAO.read_text(encoding="utf-8"))
    variaveis = {v["key"]: v["value"] for v in colecao["variable"]}
    saida = []
    for item in colecao["item"]:
        for req in item.get("item", [item]):
            url = req["request"]["url"]
            caminho = "/" + "/".join(url["path"])
            query = {q["key"]: q["value"] for q in url.get("query", [])}
            if query:
                caminho += "?" + urlencode(query, safe="{}")
            for chave, valor in variaveis.items():
                caminho = caminho.replace("{{" + chave + "}}", valor)
            corpo = req["request"].get("body", {}).get("raw")
            saida.append({
                "nome": req["name"],
                "metodo": req["request"]["method"],
                "caminho": caminho,
                "corpo": json.loads(corpo) if corpo else None,
            })
    return saida


def _um(resposta) -> dict:
    assert resposta.status_code == 200, resposta.text
    [risco] = resposta.json()
    assert sum(risco["contribuicoes"].values()) == pytest.approx(risco["score"], abs=0.002)
    assert risco["evento"]["modality"] == "fusao" and risco["evento"]["related_event_ids"]
    return risco


def _nivel(nivel: str, severidade: str) -> Callable:
    def verificar(resposta, _estado):
        risco = _um(resposta)
        assert (risco["nivel"], risco["severidade"]) == (nivel, severidade)
        return risco
    return verificar


def _demo(resposta, estado):
    risco = _nivel("alto", "alta")(resposta, estado)
    assert risco["score"] > risco["score_max_isolado"]
    assert all(risco["forca_isolada"][m] < 0.7 for m in ("sinais_vitais", "audio", "texto"))
    assert risco["concordancia"]["eixo"] == "respiratorio"
    assert risco["modalidades_ausentes"] == ["video", "movimentacao"]
    estado["scoreDemo"] = risco["score"]


def _sem_video(resposta, estado):
    risco = _nivel("alto", "info")(resposta, estado)
    assert risco["modalidades_ausentes"] == ["video", "audio", "texto", "movimentacao"]


def _janela_curta(resposta, estado):
    risco = _um(resposta)
    assert risco["contribuicoes"]["sinais_vitais"] == 0
    assert risco["score"] < estado["scoreDemo"]


def _normal(resposta, estado):
    risco = _nivel("baixo", "info")(resposta, estado)
    assert risco["score"] == 0
    assert set(risco["modalidades_presentes"]) == {"sinais_vitais", "prescricoes"}


def _dois(resposta, _estado):
    riscos = resposta.json()
    assert len(riscos) == 2 and riscos[0]["score"] >= riscos[1]["score"]


def _status(codigo: int) -> Callable:
    def verificar(resposta, _estado):
        assert resposta.status_code == codigo, resposta.text
    return verificar


def _risco_atual(resposta, _estado):
    assert resposta.status_code == 200
    assert resposta.json()["nivel"] == "alto"


def _alertas(resposta, _estado):
    assert resposta.status_code == 200
    alertas = resposta.json()
    [fusao] = [a for a in alertas if "fusao" in a["modalities"]]
    assert fusao["priority"] == "alta" and "Risco alto" in fusao["summary"]
    assert fusao["evidence"]["features"]["contrib_sinais_vitais"] > 0
    assert all(a["priority"] == "media" for a in alertas if "fusao" not in a["modalities"])


def _todos(resposta, _estado):
    assert resposta.status_code == 200
    assert "pt_00000000000000000000a007" in [r["patient_id"] for r in resposta.json()]


VERIFICACOES: dict[str, Callable] = {
    "Health": _status(200),
    "Caso de demonstração: SpO2 em queda + dispneia": _demo,
    "Sinal isolado: só sinais vitais": _nivel("medio", "info"),
    "Sinal isolado: só áudio": _nivel("baixo", "info"),
    "Modalidade ausente: paciente sem vídeo": _sem_video,
    "Janela configurável: 5 min": _janela_curta,
    "Só sem_achados": _normal,
    "Dois pacientes no mesmo lote": _dois,
    "Erro 422 - patient_id sem pseudônimo": _status(422),
    "Erro 422 - janela_min = 0": _status(422),
    "1. Publicar o caso no barramento": _nivel("alto", "alta"),
    "2. Risco atual do paciente": _risco_atual,
    "3. Alerta alta gerado pela fusão": _alertas,
    "4. Todos os pacientes": _todos,
    "Erro 404 - paciente sem achados": _status(404),
}


def test_toda_requisicao_da_colecao_tem_verificacao():
    assert {r["nome"] for r in _requisicoes()} == set(VERIFICACOES)


def test_colecao_em_dia_com_o_gerador():
    import importlib.util

    caminho = RAIZ / "scripts" / "gerar_postman_us18.py"
    spec = importlib.util.spec_from_file_location("gerar_postman_us18", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    atual = json.loads(Path(COLECAO).read_text(encoding="utf-8"))
    assert modulo.colecao() == atual, "rode: uv run python scripts/gerar_postman_us18.py"


def test_colecao_contra_o_app(monkeypatch, tmp_path):
    from app.barramento import get_barramento
    from app.main import app

    monkeypatch.setenv("ALERTA_STORE", "memoria")
    monkeypatch.setenv("ALERTA_REGRAS_PATH", str(RAIZ / "config" / "alertas_regras.yaml"))
    monkeypatch.setenv("EVENTOS_JSONL", str(tmp_path / "eventos.jsonl"))
    get_barramento.cache_clear()
    estado: dict = {}
    try:
        with TestClient(app) as client:
            for req in _requisicoes():
                resposta = client.request(req["metodo"], req["caminho"], json=req["corpo"])
                VERIFICACOES[req["nome"]](resposta, estado)
    finally:
        get_barramento.cache_clear()
