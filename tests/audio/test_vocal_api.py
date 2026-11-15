"""``POST /audio/vocal``: ingestão, biomarcadores e achados de áudio pela API (sem Azure real)."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import audio_vocal
from app.transcription_client import TranscricaoError
from audio.vocal.injecao import (
    Frase,
    ParametrosVoz,
    fala_normal,
    montar_fala,
    transcricao_sintetica,
)
from audio.vocal.respiratorio import DetectorRespiratorio
from contratos import BarramentoLocal

from .conftest import RAIZ
from .test_vocal_respiratorio import NOMES, sessao_com

TAXA = 16000
CAMPOS = {"patient_id": "10000032", "recorded_at": "2026-10-01T13:00:00Z"}


def wav_bytes(sinal, taxa=TAXA) -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, sinal, taxa, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


@pytest.fixture
def api(tmp_path, monkeypatch):
    """Só o router, com saídas em ``tmp_path`` e um barramento de teste."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(audio_vocal, "AUDIO_VOCAL_CONFIG_PATH",
                        str(RAIZ / "audio" / "vocal" / "config.yaml"))
    monkeypatch.setattr(audio_vocal, "AUDIO_INGESTAO_CONFIG_PATH",
                        str(RAIZ / "audio" / "ingestao" / "config.yaml"))
    monkeypatch.setattr(audio_vocal, "AUDIO_SAIDA_DIR", "saida/audio")
    monkeypatch.setattr(audio_vocal, "get_detector", lambda: None)
    barramento = BarramentoLocal(tmp_path / "eventos.jsonl")
    monkeypatch.setattr(audio_vocal, "get_barramento", lambda: barramento)
    audio_vocal.get_config_vocal.cache_clear()
    audio_vocal.get_config_ingestao.cache_clear()
    app = FastAPI()
    app.include_router(audio_vocal.router)
    yield TestClient(app), barramento
    audio_vocal.get_config_vocal.cache_clear()
    audio_vocal.get_config_ingestao.cache_clear()


def enviar(cliente, conteudo: bytes, *, nome="consulta.wav", params=None, **campos):
    return cliente.post(
        "/audio/vocal", params=params,
        files={"audio_file": (nome, conteudo, "audio/wav")}, data={**CAMPOS, **campos})


def com_deteccao_respiratoria(monkeypatch, cfg_vocal):
    detector = DetectorRespiratorio(
        sessao_com(Cough={2: 0.8, 3: 0.7}, Breathing={9: 0.5, 10: 0.6}), NOMES,
        cfg_vocal.respiratorio)
    monkeypatch.setattr(audio_vocal, "get_detector", lambda: detector)


# --- caminho feliz -------------------------------------------------------------------------- #


def test_rota_esta_registrada_no_app_principal():
    from app.main import app

    assert "/audio/vocal" in app.openapi()["paths"]


def test_voz_normal_devolve_classe_normal_aviso_e_metadados(api):
    cliente, barramento = api

    resposta = enviar(cliente, wav_bytes(fala_normal()[0]), origem="consulta simulada",
                      licenca="CC0 1.0")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["classe"] == "normal"
    assert corpo["classes"] == ["normal"]
    assert corpo["aviso"] == "Indicativo para avaliação clínica; não é diagnóstico."
    assert [e["event_type"] for e in corpo["eventos"]] == ["sem_achados"]
    assert corpo["publicados"] == 1
    assert corpo["patient_id"].startswith("pt_")
    assert CAMPOS["patient_id"] not in resposta.text  # o ID de origem não volta na resposta
    meta = corpo["metadados"]
    assert (meta["taxa_amostragem_hz"], meta["canais"]) == (16000, 1)
    assert meta["duracao_s"] == pytest.approx(0.3 + 4 * (2.4 + 0.5), abs=0.1)
    assert (meta["origem"], meta["licenca"]) == ("consulta simulada", "CC0 1.0")
    assert corpo["biomarcadores"]["hnr_db"] > 20
    assert barramento.total_publicados == 1


def test_o_audio_enviado_e_preservado_byte_a_byte(api, tmp_path):
    cliente, _ = api
    conteudo = wav_bytes(fala_normal()[0])

    corpo = enviar(cliente, conteudo, audio_id="gravacao-01").json()

    original = tmp_path / "saida" / "audio" / "gravacao-01" / "original.wav"
    assert hashlib.sha256(original.read_bytes()).hexdigest() == hashlib.sha256(conteudo).hexdigest()
    assert corpo["metadados"]["original_preservado"] is True
    assert (original.parent / "convertido.wav").is_file()


def test_detalhes_traz_o_relatorio_com_criterios_e_segmentos(api):
    cliente, _ = api

    corpo = enviar(cliente, wav_bytes(fala_normal()[0]), params={"detalhes": "true"}).json()

    detalhes = corpo["detalhes"]
    assert {c["criterio"] for c in detalhes["criterios"]} >= {"pausas_altas", "hnr_baixo"}
    assert detalhes["fonte_segmentos"] == "vad"
    # sem transcrição, frases separadas por pausas menores que 1 s formam um só segmento...
    assert len(detalhes["segmentos"]) == 1
    # ...e as 3 pausas entre elas aparecem como pausas internas dele
    assert detalhes["biomarcadores"]["n_pausas"] == 3


def test_sem_detalhes_o_relatorio_nao_vem(api):
    cliente, _ = api

    assert "detalhes" not in enviar(cliente, wav_bytes(fala_normal()[0])).json()


def test_publicar_false_nao_toca_no_barramento(api, tmp_path):
    cliente, barramento = api

    corpo = enviar(cliente, wav_bytes(fala_normal()[0]), params={"publicar": "false"}).json()

    assert corpo["publicados"] == 0
    assert barramento.total_publicados == 0
    assert not (tmp_path / "eventos.jsonl").exists()


def test_achado_alto_chega_ao_assinante_do_barramento(api, monkeypatch, cfg_vocal):
    """O assinante do barramento compartilhado é o motor de alertas (US19)."""
    cliente, barramento = api
    recebidos = []
    barramento.assinar("coletor", recebidos.append)
    com_deteccao_respiratoria(monkeypatch, cfg_vocal)
    # Sem transcrição não há taxa de fala; o 3º critério (para a severidade alta) é a voz
    # soprosa (HNR baixo), que não depende dela.
    sinal, _ = montar_fala([Frase(1.5, 3, 1.8) for _ in range(4)], ParametrosVoz(snr_db=12.0))

    corpo = enviar(cliente, wav_bytes(sinal), params={"detalhes": "true"}).json()

    assert corpo["classe"] == "dificuldade_respiratoria"
    alto = next(e for e in corpo["eventos"] if e["event_type"] == "dificuldade_respiratoria")
    assert alto["severity"] == "alta"
    assert alto["evidence"]["summary"].startswith("Indicativo para avaliação clínica")
    assert {e.event_type for e in recebidos} >= {"dificuldade_respiratoria", "evento_respiratorio"}
    assert corpo["detalhes"]["respiratorio"]["disponivel"] is True


# --- transcrição do Azure Speech ------------------------------------------------------------ #


def test_transcrever_usa_o_servico_e_pseudonimiza_o_paciente(api, monkeypatch):
    cliente, _ = api
    sinal, falas = fala_normal()
    chamadas = []

    def falso(convertido, **kw):
        chamadas.append(kw)
        return transcricao_sintetica(falas)

    monkeypatch.setattr(audio_vocal, "require_azure_speech_key", lambda: "k")
    monkeypatch.setattr(audio_vocal, "require_azure_speech_endpoint", lambda: "https://e")
    monkeypatch.setattr(audio_vocal, "_transcritor_da_api", falso)

    corpo = enviar(cliente, wav_bytes(sinal), transcrever="true", medico_falante="2",
                   params={"detalhes": "true"}).json()

    assert corpo["detalhes"]["fonte_segmentos"] == "transcricao"
    assert corpo["biomarcadores"]["taxa_fala_pps"] > 2
    assert chamadas[0]["patient_id"].startswith("pt_")
    assert chamadas[0]["medico_falante"] == 2


def test_transcrever_sem_credencial_do_azure_responde_503_e_nao_grava_nada(api, tmp_path, monkeypatch):
    cliente, _ = api

    def sem_chave():
        raise RuntimeError("A variável AZURE_SPEECH_KEY não foi definida no arquivo .env.")

    monkeypatch.setattr(audio_vocal, "require_azure_speech_key", sem_chave)

    resposta = enviar(cliente, wav_bytes(fala_normal()[0]), transcrever="true")

    assert resposta.status_code == 503
    assert "AZURE_SPEECH_KEY" in resposta.json()["detail"]
    assert not (tmp_path / "saida").exists()


def test_erro_do_azure_speech_vira_o_mesmo_codigo_de_transcription(api, monkeypatch):
    cliente, _ = api
    monkeypatch.setattr(audio_vocal, "require_azure_speech_key", lambda: "k")
    monkeypatch.setattr(audio_vocal, "require_azure_speech_endpoint", lambda: "https://e")

    def estoura(*a, **k):
        raise TranscricaoError("limite", status_code=429, tentativas=3)

    monkeypatch.setattr(audio_vocal, "transcrever", estoura)

    resposta = enviar(cliente, wav_bytes(fala_normal()[0]), transcrever="true")

    assert resposta.status_code == 429


def test_dois_falantes_sem_medico_falante_responde_422(api, monkeypatch):
    cliente, _ = api
    sinal, falas = fala_normal()
    transcricao = transcricao_sintetica(falas)
    transcricao["falas"][1]["falante"] = 2
    monkeypatch.setattr(audio_vocal, "require_azure_speech_key", lambda: "k")
    monkeypatch.setattr(audio_vocal, "require_azure_speech_endpoint", lambda: "https://e")
    monkeypatch.setattr(audio_vocal, "_transcritor_da_api", lambda *a, **k: transcricao)

    resposta = enviar(cliente, wav_bytes(sinal), transcrever="true")

    assert resposta.status_code == 422
    assert "medico_falante" in resposta.json()["detail"]


# --- entradas inválidas --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("nome", "conteudo", "status", "trecho"),
    [
        ("notas.txt", b"nao e audio", 400, "não suportado"),
        ("vazio.wav", b"", 422, "audio_vazio"),
        ("lixo.wav", bytes(range(256)) * 16, 422, "audio_corrompido"),
        ("curto.wav", wav_bytes(np.full(2000, 0.2, np.float32)), 422, "audio_curto"),
        ("mudo.wav", wav_bytes(np.zeros(TAXA * 2, np.float32)), 422, "audio_vazio"),
    ],
)
def test_audio_invalido_tem_status_e_codigo_claros(api, nome, conteudo, status, trecho):
    cliente, barramento = api

    resposta = enviar(cliente, conteudo, nome=nome)

    assert resposta.status_code == status, resposta.text
    assert trecho in resposta.json()["detail"]
    assert barramento.total_publicados == 0


def test_so_ruido_de_ambiente_responde_422(api):
    cliente, _ = api
    ruido = (0.0005 * np.random.default_rng(0).standard_normal(TAXA * 3)).astype(np.float32)

    resposta = enviar(cliente, wav_bytes(ruido))

    assert resposta.status_code == 422
    assert "nenhum segmento de fala" in resposta.json()["detail"]


@pytest.mark.parametrize(
    "campos",
    [
        {"recorded_at": "2026-10-01T13:00:00"},
        {"recorded_at": "2026-10-01T13:00:00-03:00"},
        {"recorded_at": "ontem"},
        {"audio_id": "id com espaco"},
    ],
)
def test_metadados_invalidos_respondem_422_sem_gravar_o_audio(api, tmp_path, campos):
    cliente, _ = api

    resposta = enviar(cliente, wav_bytes(fala_normal()[0]), **campos)

    assert resposta.status_code == 422
    assert not (tmp_path / "saida").exists()


@pytest.mark.parametrize("audio_id", ["..", ".", ".oculto", "-comeca-com-hifen"])
def test_audio_id_que_sairia_da_pasta_de_saida_e_recusado(api, tmp_path, audio_id):
    """O padrão do contrato aceita '..'; usado como pasta, escreveria fora de saida/audio."""
    cliente, _ = api

    resposta = enviar(cliente, wav_bytes(fala_normal()[0]), audio_id=audio_id)

    assert resposta.status_code == 422
    assert "audio_id_invalido" in resposta.json()["detail"]
    assert not (tmp_path / "saida").exists()
    assert not (tmp_path / "original.wav").exists()
    assert not (tmp_path / "saida" / "original.wav").exists()


def test_audio_id_repetido_nao_sobrescreve_o_original_preservado(api, tmp_path):
    cliente, _ = api
    primeiro = wav_bytes(fala_normal()[0])
    assert enviar(cliente, primeiro, audio_id="gravacao-01").status_code == 200

    segundo = enviar(cliente, wav_bytes(fala_normal(semente=5)[0]), audio_id="gravacao-01")

    assert segundo.status_code == 409
    original = tmp_path / "saida" / "audio" / "gravacao-01" / "original.wav"
    assert original.read_bytes() == primeiro


def test_upload_acima_do_limite_responde_413_e_nao_deixa_resto(api, tmp_path, monkeypatch):
    cliente, _ = api
    monkeypatch.setattr(audio_vocal, "AUDIO_MAX_UPLOAD_MB", 0)

    resposta = enviar(cliente, wav_bytes(fala_normal()[0]), audio_id="grande")

    assert resposta.status_code == 413
    assert not (tmp_path / "saida" / "audio" / "grande").exists()


def test_sem_pseudonym_key_responde_503(api, monkeypatch, tmp_path):
    cliente, _ = api
    monkeypatch.delenv("PSEUDONYM_KEY", raising=False)

    resposta = enviar(cliente, wav_bytes(fala_normal()[0]))

    assert resposta.status_code == 503
    assert "PSEUDONYM_KEY" in resposta.json()["detail"]
    assert not (tmp_path / "saida").exists()


def test_config_ausente_responde_503(api, monkeypatch, tmp_path):
    cliente, _ = api
    monkeypatch.setattr(audio_vocal, "AUDIO_VOCAL_CONFIG_PATH", str(tmp_path / "nao_existe.yaml"))
    audio_vocal.get_config_vocal.cache_clear()

    resposta = enviar(cliente, wav_bytes(fala_normal()[0]))

    assert resposta.status_code == 503
    assert "indisponível" in resposta.json()["detail"]


def test_eventos_da_resposta_sao_json_valido_do_contrato(api):
    cliente, barramento = api

    corpo = enviar(cliente, wav_bytes(fala_normal()[0])).json()

    from contratos import EventoAchado

    for evento in corpo["eventos"]:
        EventoAchado.model_validate(evento)
    linhas = Path(barramento.caminho).read_text(encoding="utf-8").splitlines()
    assert json.loads(linhas[0])["modality"] == "audio"
