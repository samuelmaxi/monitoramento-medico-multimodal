"""Painel HTML, endpoint de latência e medição ponta a ponta (US19)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from alertas import cenario_news2_emergencia, evento_sintetico
from alertas.ack import gerar_token
from alertas.armazenamento import MemoriaStore
from alertas.latencia import resumo_latencia
from alertas.painel import contagens, renderizar_painel
from contratos import Modalidade, Severidade
from scripts.medir_latencia_alertas import _servico_local, _ServidorLocal, medir

from .conftest import alerta_base, processar


@pytest.fixture
def api(client, montar, email, webhook):
    store = MemoriaStore()
    servico = montar(store, [email, webhook])
    client.app.state.alertas = servico
    yield client, servico, store
    client.app.state.alertas = None


def test_dashboard_lista_alerta_com_os_campos_minimos(api, relogio):
    client, servico, _ = api
    servico.motor.receber(cenario_news2_emergencia(relogio()))
    processar(servico)

    resposta = client.get("/alerts/dashboard")

    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("text/html")
    for trecho in ("news2_emergencia", "NEWS2 = 8", "UTI-01", "sinais_vitais", "ALTA",
                   "Avaliação de emergência", "enviado", "aguardando"):
        assert trecho in resposta.text
    assert "pt_" in resposta.text


def test_dashboard_filtra_por_prioridade_e_mostra_reconhecimento(api, relogio):
    client, servico, store = api
    servico.motor.receber(cenario_news2_emergencia(relogio()))
    servico.motor.receber(evento_sintetico(
        Modalidade.VIDEO, "postura_fora_da_faixa", severidade=Severidade.BAIXA, agora=relogio()
    ))
    processar(servico)
    [alta] = store.listar(prioridade="alta")
    servico.motor.reconhecer(alta.id, por="enfermagem")

    so_baixa = client.get("/alerts/dashboard", params={"prioridade": "baixa"}).text
    assert "postura_fora_da_faixa" in so_baixa and "news2_emergencia" not in so_baixa

    reconhecidos = client.get("/alerts/dashboard", params={"status": "ack"}).text
    assert "por enfermagem" in reconhecidos


def test_dashboard_vazio_e_sem_alertas_enviados(api):
    client, _, _ = api
    texto = client.get("/alerts/dashboard").text
    assert "Nenhum alerta" in texto
    assert "sem alertas enviados ainda" in texto


def test_dashboard_sem_servico_responde_503(client):
    client.app.state.alertas = None
    assert client.get("/alerts/dashboard").status_code == 503
    assert client.get("/alerts/latencia").status_code == 503


def test_painel_escapa_conteudo_do_achado(relogio):
    alerta = alerta_base(relogio(), summary="<script>alert(1)</script>", bed_id="<b>L1</b>")
    texto = renderizar_painel([alerta], resumo_latencia([alerta]))
    assert "<script>alert(1)" not in texto
    assert "&lt;script&gt;" in texto and "&lt;b&gt;L1" in texto


def test_painel_nao_expoe_link_de_reconhecimento(api, relogio):
    client, servico, store = api
    servico.motor.receber(cenario_news2_emergencia(relogio()))
    processar(servico)
    [alerta] = store.listar()
    token = gerar_token(alerta.id, servico.segredo_ack)
    assert token not in client.get("/alerts/dashboard").text
    assert "/ack" not in client.get("/alerts/dashboard").text


def test_contagens_por_prioridade_e_status(relogio):
    a = alerta_base(relogio())
    b = alerta_base(relogio(), id="00000000-0000-4000-8000-000000000002", priority="media",
                    status="ack")
    resumo = contagens([a, b])
    assert resumo["total"] == 2 and resumo["nao_reconhecidos"] == 1
    assert resumo["por_prioridade"] == {"alta": 1, "media": 1, "baixa": 0}


def test_endpoint_de_latencia_reporta_p95_e_meta(api, relogio):
    client, servico, _ = api
    servico.motor.receber(cenario_news2_emergencia(relogio()))
    relogio.avancar(seconds=3)
    processar(servico)

    corpo = client.get("/alerts/latencia").json()

    assert corpo["amostras"] == 1
    assert corpo["p95_s"] == pytest.approx(3.0)
    assert corpo["meta_p95_s"] == 10.0 and corpo["atendida"] is True
    assert corpo["bases"] == {"ingested_at": 1}


def test_latencia_acima_da_meta_nao_e_atendida(relogio):
    base = relogio()
    lentos = [
        alerta_base(base, id=f"00000000-0000-4000-8000-00000000000{i}", ingested_at=base,
                    sent_at=base + timedelta(seconds=15))
        for i in range(1, 4)
    ]
    assert resumo_latencia(lentos)["atendida"] is False


def test_latencia_sem_amostras_nao_tem_veredito():
    assert resumo_latencia([])["atendida"] is None


def test_ponta_a_ponta_p95_dentro_da_meta_do_dod():
    """Ingestão → barramento → motor → worker → webhook HTTP real, com o relógio real."""
    servidor = _ServidorLocal()
    try:
        resumo = asyncio.run(medir(_servico_local(servidor.url), quantidade=30, intervalo_s=0.005))
    finally:
        servidor.fechar()

    assert resumo["amostras"] == 30
    assert resumo["p95_s"] <= 10.0
    assert resumo["atendida"] is True
