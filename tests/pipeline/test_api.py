"""Rota do pipeline ponta a ponta (US20): POST /pipeline/executar e consultas da execução."""

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app import config as app_config
from app.main import app

ETAPAS = ["video", "relatorio_video", "audio", "sinais_vitais", "prescricoes", "movimentacao", "fusao", "alertas"]


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


# --------------------------------------------------------------------------- #
# Relatório de desvios do vídeo (US09) na execução
# --------------------------------------------------------------------------- #


def _executar_com_video(client, video, **dados):
    dados = {"sinais_fonte": "sintetico", "modo": "real", **dados}
    with video.open("rb") as f:
        return client.post(
            "/pipeline/executar", data=dados, files={"video_file": (video.name, f, "video/x-msvideo")}
        )


def test_video_real_traz_o_relatorio_de_desvios(client, video_sintetico, yolo_falso):
    yolo_falso([("entrada_area_critica", 0.5, "borda_leito"),
                ("saida_area_critica", 5.0, "borda_leito")])
    r = _executar_com_video(client, video_sintetico())
    assert r.status_code == 200, r.text
    corpo = r.json()
    eid = corpo["execucao_id"]

    rv = corpo["relatorio_video"]
    assert rv["total_desvios"] == 1
    assert rv["por_severidade"]["media"] == 1
    assert rv["json_url"] == f"/pipeline/execucoes/{eid}/relatorio-video"
    assert rv["html_url"] == f"/pipeline/execucoes/{eid}/relatorio-video/html"
    assert client.get(f"/pipeline/execucoes/{eid}").json() == corpo

    js = client.get(rv["json_url"])
    assert js.status_code == 200
    assert js.json()["video"]["id"] == "video_leito"
    assert js.json()["resumo"]["total_desvios"] == 1

    html = client.get(rv["html_url"])
    assert html.status_code == 200
    assert html.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in html.headers["content-security-policy"]
    assert "<script" not in html.text.lower()
    assert "joao" not in html.text.lower()

    eventos = client.get(f"/pipeline/execucoes/{eid}/eventos").json()
    assert "presenca_indevida_area_critica" in {e["event_type"] for e in eventos}


def test_sem_video_real_o_relatorio_de_video_da_404(client):
    corpo = _executar(client).json()
    eid = corpo["execucao_id"]
    assert corpo["relatorio_video"] is None
    assert client.get(f"/pipeline/execucoes/{eid}/relatorio-video").status_code == 404
    assert client.get(f"/pipeline/execucoes/{eid}/relatorio-video/html").status_code == 404


def test_relatorio_de_video_com_id_malformado_da_404(client):
    assert client.get("/pipeline/execucoes/..%2F..%2Fx/relatorio-video").status_code == 404
    assert client.get("/pipeline/execucoes/zzzzzzzzzzzz/relatorio-video/html").status_code == 404


def test_medico_falante_0_e_aceito_e_negativo_e_recusado(client):
    assert _executar(client, medico_falante="0").status_code == 200
    assert _executar(client, medico_falante="-1").status_code == 422


# --------------------------------------------------------------------------- #
# Modo real: só as entradas enviadas
# --------------------------------------------------------------------------- #


def test_modo_real_so_com_video_deixa_as_outras_modalidades_fora_da_resposta(
    client, video_sintetico, yolo_falso
):
    yolo_falso([("entrada_area_critica", 0.5, "borda_leito"),
                ("saida_area_critica", 5.0, "borda_leito")])
    corpo = _executar_com_video(client, video_sintetico()).json()

    assert [e["etapa"] for e in corpo["etapas"]] == [
        "video", "relatorio_video", "movimentacao", "fusao", "alertas"
    ]
    assert corpo["etapas_com_falha"] == []
    assert {"audio", "sinais_vitais", "prescricoes"} <= set(corpo["risco"]["modalidades_ausentes"])
    eventos = client.get(f"/pipeline/execucoes/{corpo['execucao_id']}/eventos").json()
    assert eventos and not any(e["is_synthetic"] for e in eventos)
    assert corpo["risco"]["evento"]["is_synthetic"] is False


def test_max_quadros_video_chega_ao_yolo_e_vazio_e_o_video_inteiro(
    client, video_sintetico, monkeypatch
):
    from types import SimpleNamespace

    from pipeline.etapas.video import EtapaVideo

    pedidos = []

    def falso(self, contexto, caminho, agora):
        pedidos.append(contexto.config.max_frames_video)
        return [], SimpleNamespace(quadros_lidos=10, deteccoes_total=0, fps=10.0)

    monkeypatch.setattr(EtapaVideo, "_processar_yolo", falso)
    video = video_sintetico()
    assert _executar_com_video(client, video).status_code == 200
    assert _executar_com_video(client, video, max_quadros_video="120").status_code == 200
    assert _executar_com_video(client, video, max_quadros_video="0").status_code == 422
    assert pedidos == [None, 120]
