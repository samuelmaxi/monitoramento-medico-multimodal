"""Armazenamento (memória e JSONL), formatação dos textos e canais de envio (e-mail e webhook)."""

from __future__ import annotations

import asyncio
import http.server
import json
import threading
import urllib.error
import uuid
from typing import ClassVar

import pytest

from alertas import StatusAlerta, cenario_news2_emergencia, evento_sintetico
from alertas.ack import gerar_token, verificar_token
from alertas.armazenamento import ArmazenamentoJsonl, MemoriaStore
from alertas.modelos import TipoAuditoria, novo_evento_de_alerta
from alertas.notificadores import NotificadorEmail, NotificadorWebhook, email_smtp, formatacao
from contratos import Modalidade, Severidade

from .conftest import alerta_base

# --- armazenamento ---------------------------------------------------------- #

def test_jsonl_recarrega_alertas_e_auditoria_do_disco(tmp_path, montar, relogio):
    store = ArmazenamentoJsonl(tmp_path)
    servico = montar(store)
    servico.motor.receber(cenario_news2_emergencia(relogio()))
    [alerta] = store.listar()

    recarregado = ArmazenamentoJsonl(tmp_path)

    assert recarregado.obter(alerta.id) == alerta
    assert [e.type for e in recarregado.eventos(alerta.id)] == [TipoAuditoria.CRIADO]
    assert (tmp_path / "alertas.jsonl").exists()
    assert (tmp_path / "alertas_eventos.jsonl").exists()


def test_jsonl_ultima_versao_de_cada_alerta_vence(tmp_path, relogio):
    store = ArmazenamentoJsonl(tmp_path)
    alerta = alerta_base(relogio())
    store.salvar(alerta)
    alerta.status = StatusAlerta.ENVIADO
    store.salvar(alerta)

    recarregado = ArmazenamentoJsonl(tmp_path)

    assert recarregado.obter(alerta.id).status is StatusAlerta.ENVIADO
    assert len(recarregado.listar()) == 1


def test_buscar_aberto_ignora_alerta_reconhecido(relogio):
    store = MemoriaStore()
    alerta = alerta_base(relogio(), status=StatusAlerta.RECONHECIDO)
    store.salvar(alerta)

    assert store.buscar_aberto(alerta.dedup_key) is None


def test_eventos_de_alerta_sao_separados_por_alerta(relogio):
    store = MemoriaStore()
    a, b = alerta_base(relogio()), alerta_base(relogio(), id=uuid.uuid4())
    for alerta in (a, b):
        store.salvar(alerta)
        store.registrar_evento(novo_evento_de_alerta(alerta, TipoAuditoria.CRIADO, relogio()))

    assert len(store.eventos(a.id)) == 1
    assert len(store.eventos(b.id)) == 1


# --- formatação -------------------------------------------------------------- #

@pytest.fixture
def alerta_news2(montar, relogio):
    store = MemoriaStore()
    servico = montar(store)
    servico.motor.receber(cenario_news2_emergencia(relogio()))
    [alerta] = store.listar()
    return alerta


def test_texto_traz_os_campos_minimos_e_nao_traz_nome(alerta_news2):
    texto = formatacao.corpo_texto(alerta_news2, lambda a: "https://ex/ack")

    assert "Paciente (pseudônimo): pt_" in texto
    assert "Leito: UTI-01" in texto
    assert "Modalidade: sinais_vitais" in texto
    assert "Anomalia: news2_emergencia" in texto
    assert "Severidade: ALTA" in texto
    assert "Evidência: NEWS2 = 8" in texto
    assert "Valores observados: spo2=89" in texto
    assert "Horário do evento:" in texto
    assert "Ação sugerida: Avaliação de emergência" in texto
    assert "Reconhecer o alerta: https://ex/ack" in texto


def test_assunto_leva_prioridade_leito_e_marca_simulado(alerta_news2):
    assunto = formatacao.assunto(alerta_news2)

    assert assunto.startswith("[SIMULADO] [ALTA] NEWS2 = 8")
    assert assunto.endswith("· leito UTI-01")


def test_html_escapa_conteudo_do_achado(montar, relogio):
    store = MemoriaStore()
    servico = montar(store)
    servico.motor.receber(evento_sintetico(
        Modalidade.TEXTO, "termo_critico", severidade=Severidade.ALTA,
        resumo="<b>termo</b> citado", agora=relogio(),
    ))
    [alerta] = store.listar()

    corpo = formatacao.corpo_html(alerta)

    assert "&lt;b&gt;termo&lt;/b&gt;" in corpo
    assert "<b>termo</b>" not in corpo


# --- canal de e-mail -------------------------------------------------------- #

class _SmtpFalso:
    instancias: ClassVar[list[_SmtpFalso]] = []

    def __init__(self, servidor, porta, timeout):
        self.servidor, self.porta = servidor, porta
        self.enviadas: list = []
        _SmtpFalso.instancias.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.tls = True

    def login(self, usuario, senha):
        self.login_com = (usuario, senha)

    def send_message(self, mensagem):
        self.enviadas.append(mensagem)


def test_email_envia_para_destinatarios_simulados_quando_configurados(monkeypatch, alerta_news2):
    _SmtpFalso.instancias.clear()
    monkeypatch.setattr(email_smtp.smtplib, "SMTP", _SmtpFalso)
    canal = NotificadorEmail(
        servidor="smtp.teste", porta=587, remetente="alertas@teste",
        destinatarios=("equipe@teste",), destinatarios_simulado=("qa@teste",),
        usuario="alertas@teste", senha="segredo", starttls=True,
        ligacao_ack=lambda a: f"https://ex/alerts/{a.id}/ack?token=abc",
    )

    asyncio.run(canal.enviar(alerta_news2))

    [smtp] = _SmtpFalso.instancias
    assert smtp.tls is True
    assert smtp.login_com == ("alertas@teste", "segredo")
    [mensagem] = smtp.enviadas
    assert mensagem["To"] == "qa@teste"
    assert mensagem["Subject"].startswith("[SIMULADO] [ALTA]")
    assert "Reconhecer o alerta: https://ex/alerts/" in mensagem.get_body(("plain",)).get_content()


def test_email_sem_destinatarios_falha_explicitamente(alerta_news2):
    canal = NotificadorEmail(servidor="smtp.teste", porta=587, remetente="a@b", destinatarios=())

    with pytest.raises(RuntimeError, match="nenhum destinatário"):
        asyncio.run(canal.enviar(alerta_news2))


# --- canal de webhook ------------------------------------------------------- #

def test_webhook_posta_texto_em_json_e_falha_com_erro_http(alerta_news2):
    recebidos: list[tuple[int, bytes]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            corpo = self.rfile.read(int(self.headers["Content-Length"]))
            recebidos.append((len(recebidos), corpo))
            self.send_response(500 if self.path == "/falha" else 200)
            self.end_headers()

        def log_message(self, *args):
            pass

    servidor = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{servidor.server_address[1]}"
        asyncio.run(NotificadorWebhook(f"{base}/ok").enviar(alerta_news2))
        with pytest.raises(urllib.error.HTTPError):
            asyncio.run(NotificadorWebhook(f"{base}/falha").enviar(alerta_news2))
    finally:
        servidor.shutdown()
        servidor.server_close()

    primeiro = json.loads(recebidos[0][1].decode("utf-8"))
    assert "NEWS2 = 8" in primeiro["text"]
    assert "[ALTA]" in primeiro["text"]


# --- token de reconhecimento ------------------------------------------------- #

def test_token_de_reconhecimento_so_vale_para_o_proprio_alerta():
    alerta_id = uuid.uuid4()
    token = gerar_token(alerta_id, "segredo")

    assert verificar_token(alerta_id, token, "segredo")
    assert not verificar_token(uuid.uuid4(), token, "segredo")
    assert not verificar_token(alerta_id, token, "outro-segredo")
    assert not verificar_token(alerta_id, "xyz", "segredo")

