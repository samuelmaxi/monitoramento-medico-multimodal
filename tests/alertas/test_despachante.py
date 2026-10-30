"""Despachante: retentativa por canal, esgotamento, recuperação após queda e entrega entre threads."""

from __future__ import annotations

import asyncio
import contextlib

from alertas import StatusAlerta, TipoAuditoria, cenario_news2_emergencia, evento_sintetico
from alertas.armazenamento import ArmazenamentoJsonl, MemoriaStore
from alertas.notificadores import NotificadorFake
from contratos import Modalidade, Severidade

from .conftest import processar


def test_falha_de_um_canal_nao_impede_o_outro_e_so_retenta_o_que_falhou(montar, email, relogio):
    webhook = NotificadorFake("webhook", falhas=1)
    store = MemoriaStore()
    servico = montar(store, [email, webhook])

    servico.motor.receber(cenario_news2_emergencia(relogio()))
    processar(servico)

    [alerta] = store.listar()
    assert alerta.status is StatusAlerta.FALHOU
    assert alerta.pending_channels == ["webhook"]
    assert len(email.enviados) == 1

    relogio.avancar(seconds=10)  # backoff da primeira tentativa: 5 s
    processar(servico)

    [alerta] = store.listar()
    assert alerta.status is StatusAlerta.ENVIADO
    assert alerta.pending_channels == []
    assert len(email.enviados) == 1  # o canal que já deu certo não recebe de novo
    assert len(webhook.enviados) == 1


def test_canal_nao_configurado_esgota_as_tentativas_e_para(montar, email, relogio):
    store = MemoriaStore()
    servico = montar(store, [email], tentativas_max=2)  # alta exige webhook, que não existe aqui

    servico.motor.receber(cenario_news2_emergencia(relogio()))
    processar(servico)
    relogio.avancar(seconds=6)
    processar(servico)
    relogio.avancar(hours=1)
    processar(servico)  # esgotado: não tenta mais

    [alerta] = store.listar()
    assert alerta.status is StatusAlerta.FALHOU
    assert alerta.retries == 2
    assert alerta.next_attempt_at is None
    assert alerta.pending_channels == ["webhook"]
    ultima = [e for e in store.eventos(alerta.id) if e.type is TipoAuditoria.FALHOU][-1]
    assert ultima.detail == "webhook: canal não configurado (tentativa 2 de 2)"


def test_retentativa_respeita_o_horario_do_backoff(montar, email, relogio):
    webhook_falho = NotificadorFake("webhook", falhas=1)
    store = MemoriaStore()
    servico = montar(store, [email, webhook_falho])

    servico.motor.receber(cenario_news2_emergencia(relogio()))
    processar(servico)
    relogio.avancar(seconds=1)  # ainda antes de 5 s
    processar(servico)

    assert webhook_falho.tentativas == 1
    assert store.listar()[0].status is StatusAlerta.FALHOU


def test_baixa_fica_so_no_painel_sem_chamar_canal(montar, email, relogio):
    store = MemoriaStore()
    servico = montar(store, [email])

    servico.motor.receber(evento_sintetico(Modalidade.SINAIS_VITAIS, "desvio_baseline",
                                           severidade=Severidade.BAIXA, agora=relogio()))
    processar(servico)

    assert store.listar()[0].status is StatusAlerta.REGISTRADO
    assert email.tentativas == 0


def test_recupera_alertas_pendentes_depois_de_queda_do_processo(tmp_path, montar, relogio):
    # Primeira execução: o alerta é gravado (outbox) e o processo cai antes de enviar.
    servico_antes = montar(ArmazenamentoJsonl(tmp_path), [NotificadorFake("email")])
    servico_antes.motor.receber(cenario_news2_emergencia(relogio()))

    # Segunda execução: o armazenamento é relido do disco e o alerta pendente é enviado.
    email_depois = NotificadorFake("email")
    webhook_depois = NotificadorFake("webhook")
    store_depois = ArmazenamentoJsonl(tmp_path)
    servico_depois = montar(store_depois, [email_depois, webhook_depois])
    processar(servico_depois)

    assert store_depois.listar()[0].status is StatusAlerta.ENVIADO
    assert len(email_depois.enviados) == 1
    assert len(webhook_depois.enviados) == 1


def test_alerta_publicado_em_outra_thread_chega_ao_worker(montar, email, relogio):
    """O barramento entrega na thread de quem publicou. O worker do event loop tem que receber."""
    webhook = NotificadorFake("webhook")
    servico = montar(MemoriaStore(), [email, webhook])

    async def cenario() -> None:
        worker = asyncio.create_task(servico.executar())
        for _ in range(200):  # espera o worker subir e registrar o loop
            if servico.despachante._loop is not None:
                break
            await asyncio.sleep(0.01)
        await asyncio.to_thread(servico.motor.receber, cenario_news2_emergencia(relogio()))
        for _ in range(300):
            if email.enviados and webhook.enviados:
                break
            await asyncio.sleep(0.01)
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker

    asyncio.run(cenario())

    assert len(email.enviados) == 1
    assert len(webhook.enviados) == 1
