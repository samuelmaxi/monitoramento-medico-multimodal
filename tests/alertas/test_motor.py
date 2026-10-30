"""Motor de alertas: cenário do DoD, deduplicação, escalonamento e agnosticismo de modalidade."""

from __future__ import annotations

import pytest

from alertas import (
    StatusAlerta,
    TipoAuditoria,
    cenario_news2_emergencia,
    evento_sintetico,
)
from alertas.armazenamento import MemoriaStore
from alertas.notificadores import formatacao
from contratos import SEM_ACHADOS, TIPOS_DE_EVENTO, Modalidade, Severidade

from .conftest import processar
from .fabrica_eventos import evento

PARES = [(m, t) for m, tipos in TIPOS_DE_EVENTO.items() for t in tipos if t != SEM_ACHADOS]


def _tipos_de_auditoria(store, alerta_id) -> list[TipoAuditoria]:
    return [e.type for e in store.eventos(alerta_id)]


class TestCenarioDoD:
    """Cenário do DoD: SpO2 caindo e NEWS2 = 8 geram exatamente um alerta de prioridade alta."""

    def test_gera_exatamente_um_alerta_alta_e_envia(self, montar, email, webhook, relogio):
        store = MemoriaStore()
        servico = montar(store, [email, webhook])

        servico.motor.receber(cenario_news2_emergencia(relogio()))
        processar(servico)

        [alerta] = store.listar()
        assert alerta.priority is Severidade.ALTA
        assert alerta.status is StatusAlerta.ENVIADO
        assert alerta.bed_id == "UTI-01"
        assert alerta.rule_origin == "regra:sinais_vitais/news2_emergencia"
        assert len(email.enviados) == 1 and len(webhook.enviados) == 1

    def test_duplicata_dentro_da_janela_e_suprimida(self, montar, email, webhook, relogio):
        store = MemoriaStore()
        servico = montar(store, [email, webhook])
        servico.motor.receber(cenario_news2_emergencia(relogio()))
        processar(servico)

        relogio.avancar(minutes=2)  # janela de alta: 5 minutos
        servico.motor.receber(cenario_news2_emergencia(relogio()))
        processar(servico)

        [alerta] = store.listar()
        assert alerta.occurrences == 2
        assert len(email.enviados) == 1
        assert _tipos_de_auditoria(store, alerta.id) == [
            TipoAuditoria.CRIADO, TipoAuditoria.ENVIADO, TipoAuditoria.SUPRIMIDO,
        ]

    def test_duplicata_fora_da_janela_abre_novo_alerta(self, montar, email, webhook, relogio):
        store = MemoriaStore()
        servico = montar(store, [email, webhook])
        servico.motor.receber(cenario_news2_emergencia(relogio()))
        processar(servico)

        relogio.avancar(minutes=6)
        servico.motor.receber(cenario_news2_emergencia(relogio()))
        processar(servico)

        assert len(store.listar()) == 2
        assert len(email.enviados) == 2


class TestEscalonamento:
    def test_persistencia_por_ocorrencias_escalona_baixa_para_media_e_envia(self, montar, email, relogio):
        store = MemoriaStore()
        servico = montar(store, [email])
        for _ in range(10):  # janela de baixa: 60 minutos; achados a cada minuto
            servico.motor.receber(evento(Modalidade.SINAIS_VITAIS, "desvio_baseline", Severidade.BAIXA,
                                         agora=relogio()))
            relogio.avancar(minutes=1)
        processar(servico)

        [alerta] = store.listar()
        assert alerta.priority is Severidade.MEDIA
        assert alerta.escalation_level == 1
        assert TipoAuditoria.ESCALONADO in _tipos_de_auditoria(store, alerta.id)
        assert [a.priority for a in email.enviados] == [Severidade.MEDIA]

    def test_persistencia_por_tempo_escalona_pelo_timer(self, montar, email, relogio):
        store = MemoriaStore()
        servico = montar(store, [email])
        servico.motor.receber(evento(Modalidade.SINAIS_VITAIS, "desvio_baseline", Severidade.BAIXA,
                                     agora=relogio()))
        processar(servico)
        assert email.enviados == []  # baixa só registra

        relogio.avancar(minutes=121)  # escalar_apos_min da baixa: 120
        escalados = servico.motor.varrer()
        processar(servico)

        assert len(escalados) == 1
        assert store.listar()[0].priority is Severidade.MEDIA
        assert len(email.enviados) == 1

    def test_alta_nao_escalona_por_persistencia(self, montar, email, webhook, relogio):
        store = MemoriaStore()
        servico = montar(store, [email, webhook])
        for _ in range(20):
            servico.motor.receber(cenario_news2_emergencia(relogio()))
            relogio.avancar(minutes=1)
        processar(servico)

        [alerta] = store.listar()
        assert alerta.priority is Severidade.ALTA
        assert alerta.escalation_level == 0
        assert len(email.enviados) == 1


class TestRegistroEReconhecimento:
    def test_baixa_fica_so_no_painel(self, montar, email, relogio):
        store = MemoriaStore()
        servico = montar(store, [email])
        servico.motor.receber(evento(Modalidade.VIDEO, "postura_fora_da_faixa", Severidade.BAIXA, agora=relogio()))
        processar(servico)

        [alerta] = store.listar()
        assert alerta.status is StatusAlerta.REGISTRADO
        assert email.enviados == []

    def test_reconhecido_encerra_e_novo_achado_abre_outro_alerta(self, montar, email, webhook, relogio):
        store = MemoriaStore()
        servico = montar(store, [email, webhook])
        servico.motor.receber(cenario_news2_emergencia(relogio()))
        processar(servico)
        [alerta] = store.listar()

        reconhecido = servico.motor.reconhecer(alerta.id, por="teste")
        assert reconhecido.status is StatusAlerta.RECONHECIDO
        assert reconhecido.acked_by == "teste"

        servico.motor.receber(cenario_news2_emergencia(relogio()))
        processar(servico)
        assert len(store.listar()) == 2

    def test_reconhecer_duas_vezes_nao_duplica_auditoria(self, montar, email, webhook, relogio):
        store = MemoriaStore()
        servico = montar(store, [email, webhook])
        servico.motor.receber(cenario_news2_emergencia(relogio()))
        processar(servico)
        [alerta] = store.listar()

        servico.motor.reconhecer(alerta.id, por="teste")
        servico.motor.reconhecer(alerta.id, por="outro")

        tipos = _tipos_de_auditoria(store, alerta.id)
        assert tipos.count(TipoAuditoria.RECONHECIDO) == 1

    def test_reconhecer_alerta_inexistente_devolve_none(self, montar):
        import uuid

        assert montar().motor.reconhecer(uuid.uuid4(), por="teste") is None


class TestAgnosticismoDeModalidade:
    """O motor não conhece modalidade: qualquer achado do catálogo gera alerta, mesmo sem detector."""

    @pytest.mark.parametrize(("modalidade", "tipo"), PARES, ids=[f"{m.value}/{t}" for m, t in PARES])
    def test_qualquer_achado_do_catalogo_vira_alerta_enviado(
        self, modalidade, tipo, montar, email, webhook, relogio, regras
    ):
        store = MemoriaStore()
        servico = montar(store, [email, webhook])

        servico.motor.receber(evento_sintetico(modalidade, tipo, severidade=Severidade.MEDIA, agora=relogio()))
        processar(servico)

        [alerta] = store.listar()
        assert alerta.status is StatusAlerta.ENVIADO
        assert alerta.suggested_action
        if regras.regra_para(modalidade.value, tipo) is None:
            assert alerta.rule_origin == "padrao:media"
            assert alerta.priority is Severidade.MEDIA
        assert len(email.enviados) == 1

    def test_achado_sem_ingestao_e_sem_leito_nao_quebra(self, montar, email, relogio):
        store = MemoriaStore()
        servico = montar(store, [email])

        servico.motor.receber(evento_sintetico(
            Modalidade.AUDIO, "fadiga_vocal", severidade=Severidade.MEDIA,
            com_ingestao=False, leito=None, agora=relogio(),
        ))
        processar(servico)

        [alerta] = store.listar()
        assert alerta.latency_base == "detected_at"
        assert alerta.bed_id is None
        assert "Leito: não informado" in formatacao.corpo_texto(email.enviados[0])

    @pytest.mark.parametrize(
        ("modalidade", "tipo"),
        [(Modalidade.VIDEO, "postura_fora_da_faixa"), (Modalidade.VIDEO, SEM_ACHADOS)],
    )
    def test_info_nao_gera_alerta(self, modalidade, tipo, montar, email, relogio):
        store = MemoriaStore()
        servico = montar(store, [email])

        servico.motor.receber(evento_sintetico(modalidade, tipo, severidade=Severidade.INFO, agora=relogio()))
        processar(servico)

        assert store.listar() == []
        assert email.enviados == []
