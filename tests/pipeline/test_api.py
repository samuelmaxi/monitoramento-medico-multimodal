"""Rota do pipeline ponta a ponta (US20): POST /pipeline/executar e consultas da execução."""

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app import config as app_config
from app.main import app

ETAPAS = ["video", "audio", "sinais_vitais", "prescricoes", "movimentacao", "fusao", "alertas"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "PIPELINE_API_SAIDA_DIR", tmp_path / "pipeline_api")
    monkeypatch.setenv("PSEUDONYM_KEY", "chave-de-teste-com-mais-de-32-bytes-000000")
    for var in ("AZURE_SPEECH_KEY", "AZURE_LANGUAGE_KEY", "AZURE_LANGUAGE_ENDPOINT"):
        monkeypatch.delenv(var, raising=False)
    # O lifespan da API monta o serviço de alertas a partir do ambiente. Um .env local com
    # ALERTA_STORE=cosmos faria estes testes gravarem alertas no banco real.
    monkeypatch.setenv("ALERTA_STORE", "memoria")
    monkeypatch.setenv("ALERTA_SAIDA_DIR", str(tmp_path / "alertas"))
    for var in ("COSMOS_ENDPOINT", "COSMOS_KEY", "SMTP_HOST", "ALERTA_DESTINATARIOS",
                "ALERTA_WEBHOOK_URL"):
        monkeypatch.delenv(var, raising=False)
    with TestClient(app) as c:
        yield c


def _executar(client, **dados):
    dados.setdefault("sinais_fonte", "sintetico")
    return client.post("/pipeline/executar", data=dados)


def test_executar_roda_o_fluxo_completo_e_devolve_o_risco_da_fusao(client):
    r = _executar(client)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["status"] == "sucesso"
    assert [e["etapa"] for e in corpo["etapas"]] == ETAPAS
    assert corpo["etapas_com_falha"] == []
    assert corpo["risco"]["nivel"] == "alto"
    assert set(corpo["risco"]["modalidades_presentes"]) == {
        "video", "audio", "texto", "sinais_vitais", "prescricoes", "movimentacao"
    }
    assert corpo["total_alertas"] > 0
    assert corpo["patient_id"].startswith("pt_")
    assert corpo["publicado_no_barramento"] is False


def test_resposta_diz_qual_servico_azure_cada_etapa_usou(client):
    corpo = _executar(client).json()
    audio = next(e for e in corpo["etapas"] if e["etapa"] == "audio")
    assert "demonstração" in audio["detalhes"]["servico_speech"]
    assert "simulado" in audio["detalhes"]["servico_language"].lower()


def test_consulta_da_execucao_e_dos_eventos(client):
    corpo = _executar(client).json()
    eid = corpo["execucao_id"]
    assert client.get(f"/pipeline/execucoes/{eid}").json() == corpo
    eventos = client.get(f"/pipeline/execucoes/{eid}/eventos").json()
    assert len(eventos) == corpo["total_eventos"]
    assert {e["patient_id"] for e in eventos} == {corpo["patient_id"]}


def test_execucao_inexistente_ou_id_malformado_da_404(client):
    assert client.get("/pipeline/execucoes/aaaaaaaaaaaa").status_code == 404
    assert client.get("/pipeline/execucoes/..%2F..%2Fx/eventos").status_code == 404


def test_audio_enviado_passa_pela_analise_vocal(client, tmp_path):
    t = np.arange(16000 * 4) / 16000
    wav = tmp_path / "consulta.wav"
    sf.write(str(wav), (0.4 * np.sin(2 * np.pi * 180 * t)).astype(np.float32), 16000)
    with wav.open("rb") as f:
        r = client.post(
            "/pipeline/executar",
            data={"sinais_fonte": "sintetico"},
            files={"audio_file": ("consulta.wav", f, "audio/wav")},
        )
    assert r.status_code == 200, r.text
    audio = next(e for e in r.json()["etapas"] if e["etapa"] == "audio")
    assert audio["detalhes"]["biomarcadores_vocais"].startswith("audio.vocal")


def test_formato_de_audio_invalido_da_400_e_nao_deixa_lixo(client, tmp_path):
    r = client.post(
        "/pipeline/executar",
        files={"audio_file": ("nota.txt", b"oi", "text/plain")},
    )
    assert r.status_code == 400
    assert not list((tmp_path / "pipeline_api").glob("*")) if (tmp_path / "pipeline_api").exists() else True


def test_formato_de_video_invalido_da_400(client):
    r = client.post("/pipeline/executar", files={"video_file": ("x.txt", b"oi", "text/plain")})
    assert r.status_code == 400


def test_modo_invalido_da_422(client):
    assert _executar(client, modo="turbo").status_code == 422


def test_arquivo_vazio_do_swagger_e_ignorado(client):
    r = client.post(
        "/pipeline/executar",
        data={"sinais_fonte": "sintetico"},
        files={"audio_file": ("", b"", "application/octet-stream")},
    )
    assert r.status_code == 200


def test_publicar_faz_a_fusao_ao_vivo_enxergar_o_paciente(client):
    corpo = _executar(client, publicar="true", patient_id="paciente-publicado").json()
    assert corpo["publicado_no_barramento"] is True
    ao_vivo = client.get(f"/fusao/pacientes/{corpo['patient_id']}")
    assert ao_vivo.status_code == 200
    assert ao_vivo.json()["nivel"] == "alto"


def test_sem_publicar_a_fusao_ao_vivo_nao_ve_o_paciente(client):
    corpo = _executar(client, patient_id="paciente-isolado").json()
    assert client.get(f"/fusao/pacientes/{corpo['patient_id']}").status_code == 404
