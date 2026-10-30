"""API de alertas e integração com o barramento real (o caminho que os detectores usam)."""

from __future__ import annotations

import time
import uuid

import pytest

from alertas import StatusAlerta, cenario_news2_emergencia, evento_sintetico
from alertas.ack import gerar_token
from alertas.armazenamento import MemoriaStore
from alertas.servico import NOME_ASSINANTE
from contratos import BarramentoLocal, Modalidade, Severidade

from .conftest import processar


@pytest.fixture
def api(client, montar, email, webhook, relogio):
    """Cliente da API com o serviço de alertas ligado, sem subir o lifespan."""
    store = MemoriaStore()
    servico = montar(store, [email, webhook])
    client.app.state.alertas = servico
    yield client, servico, store
    client.app.state.alertas = None


def _alerta_enviado(servico, store, relogio):
    servico.motor.receber(cenario_news2_emergencia(relogio()))
    processar(servico)
    [alerta] = store.listar()
    return alerta


def test_lista_alertas_e_filtra_por_status(api, relogio):
    client, servico, store = api
    alerta = _alerta_enviado(servico, store, relogio)

    todos = client.get("/alerts").json()
    assert [a["id"] for a in todos] == [str(alerta.id)]
    assert todos[0]["priority"] == "alta"

    assert client.get("/alerts", params={"status": "ack"}).json() == []
    assert len(client.get("/alerts", params={"prioridade": "alta"}).json()) == 1


def test_detalhe_traz_alerta_e_auditoria(api, relogio):
    client, servico, store = api
    alerta = _alerta_enviado(servico, store, relogio)

    corpo = client.get(f"/alerts/{alerta.id}").json()

    assert corpo["alerta"]["id"] == str(alerta.id)
    assert [e["type"] for e in corpo["auditoria"]] == ["created", "sent"]


def test_detalhe_inexistente_responde_404(api):
    client, _, _ = api
    assert client.get(f"/alerts/{uuid.uuid4()}").status_code == 404


def test_ack_por_link_com_token_valido_reconhece(api, relogio):
    client, servico, store = api
    alerta = _alerta_enviado(servico, store, relogio)
    token = gerar_token(alerta.id, servico.segredo_ack)

    resposta = client.get(f"/alerts/{alerta.id}/ack", params={"token": token})

    assert resposta.status_code == 200
    assert "Alerta reconhecido" in resposta.text
    assert store.obter(alerta.id).status is StatusAlerta.RECONHECIDO
    assert store.obter(alerta.id).acked_by == "link de e-mail"


def test_ack_pela_api_com_token_valido_reconhece(api, relogio):
    client, servico, store = api
    alerta = _alerta_enviado(servico, store, relogio)
    token = gerar_token(alerta.id, servico.segredo_ack)

    resposta = client.post(f"/alerts/{alerta.id}/ack", params={"token": token})

    assert resposta.status_code == 200
    assert resposta.json()["status"] == "ack"
    assert resposta.json()["acked_by"] == "api"


@pytest.mark.parametrize("token", ["token-errado", ""])
def test_ack_com_token_invalido_responde_403_e_nao_altera_o_alerta(api, relogio, token):
    client, servico, store = api
    alerta = _alerta_enviado(servico, store, relogio)

    resposta = client.post(f"/alerts/{alerta.id}/ack", params={"token": token})

    assert resposta.status_code in (403, 422)
    assert store.obter(alerta.id).status is StatusAlerta.ENVIADO


def test_token_de_outro_alerta_nao_serve(api, relogio):
    client, servico, store = api
    alerta = _alerta_enviado(servico, store, relogio)
    token_de_outro = gerar_token(uuid.uuid4(), servico.segredo_ack)

    resposta = client.post(f"/alerts/{alerta.id}/ack", params={"token": token_de_outro})

    assert resposta.status_code == 403


def test_api_responde_503_sem_motor_ligado(client, monkeypatch):
    monkeypatch.setattr(client.app.state, "alertas", None, raising=False)

    assert client.get("/alerts").status_code == 503


class TestIntegracaoComBarramento:
    """Detectores publicam no ``BarramentoLocal``; o motor assina e recebe só o que interessa."""

    def test_texto_de_alta_severidade_publicado_vira_alerta(self, montar, email, webhook, tmp_path, relogio):
        store = MemoriaStore()
        servico = montar(store, [email, webhook])
        barramento = BarramentoLocal(tmp_path / "eventos.jsonl")
        servico.assinar(barramento)

        barramento.emitir(evento_sintetico(Modalidade.TEXTO, "termo_critico",
                                           severidade=Severidade.ALTA, agora=relogio()))
        processar(servico)

        [alerta] = store.listar()
        assert alerta.modalities == ["texto"]
        assert len(email.enviados) == 1

    def test_so_um_modulo_publicando_ja_gera_alertas(self, montar, email, webhook, tmp_path, relogio):
        """Video sozinho, com os outros módulos ausentes ou em desenvolvimento, gera alertas."""
        store = MemoriaStore()
        servico = montar(store, [email, webhook])
        barramento = BarramentoLocal(tmp_path / "eventos.jsonl")
        servico.assinar(barramento)

        barramento.emitir_varios([
            evento_sintetico(Modalidade.VIDEO, "mudanca_brusca_ou_queda", severidade=Severidade.ALTA, agora=relogio()),
            evento_sintetico(Modalidade.VIDEO, "entrada_area_critica", severidade=Severidade.MEDIA, agora=relogio()),
        ])
        processar(servico)

        alertas = store.listar()
        assert len(alertas) == 2
        assert {a.modalities[0] for a in alertas} == {"video"}

    def test_info_nao_chega_ao_motor(self, montar, email, tmp_path, relogio):
        store = MemoriaStore()
        servico = montar(store, [email])
        barramento = BarramentoLocal(tmp_path / "eventos.jsonl")
        servico.assinar(barramento)

        barramento.emitir(evento_sintetico(Modalidade.VIDEO, "postura_fora_da_faixa",
                                           severidade=Severidade.INFO, agora=relogio()))
        processar(servico)

        assert store.listar() == []
        assert barramento.total_publicados == 1

    def test_motor_se_registra_com_nome_proprio(self, montar, tmp_path):
        barramento = BarramentoLocal(tmp_path / "eventos.jsonl")
        montar().assinar(barramento)

        assert [a.nome for a in barramento._assinaturas] == [NOME_ASSINANTE]


def test_lifespan_liga_o_motor_ao_barramento_que_os_roteadores_usam(monkeypatch, tmp_path):
    """Sobe o app de verdade: o motor assina o mesmo barramento que os detectores publicam."""
    from fastapi.testclient import TestClient

    from app.barramento import get_barramento
    from app.main import app

    from .conftest import RAIZ

    monkeypatch.setenv("ALERTA_STORE", "memoria")
    monkeypatch.setenv("ALERTA_REGRAS_PATH", str(RAIZ / "config" / "alertas_regras.yaml"))
    monkeypatch.setenv("EVENTOS_JSONL", str(tmp_path / "eventos.jsonl"))
    get_barramento.cache_clear()
    try:
        with TestClient(app) as client:
            get_barramento().emitir(cenario_news2_emergencia())
            alertas = []
            for _ in range(300):  # o worker roda no event loop do lifespan
                alertas = client.get("/alerts").json()
                if alertas:
                    break
                time.sleep(0.01)

        assert len(alertas) == 1
        assert alertas[0]["modalities"] == ["sinais_vitais"]
        assert alertas[0]["priority"] == "alta"
    finally:
        get_barramento.cache_clear()
