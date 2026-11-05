"""CosmosStore contra a conta real do Azure (``COSMOS_ENDPOINT`` e ``COSMOS_KEY`` no .env).

Sem essas variáveis os testes são pulados. Cada teste usa um paciente de teste com
pseudônimo aleatório e apaga, no fim, tudo o que gravou nos dois containers.
"""

from __future__ import annotations

import os
import random
import uuid
from datetime import timedelta

import pytest

from alertas import StatusAlerta, TipoAuditoria, evento_sintetico
from alertas.armazenamento.cosmos import CosmosStore
from alertas.modelos import EventoDeAlerta, novo_evento_de_alerta
from contratos import Modalidade, Severidade, pseudonimizar_paciente

from .conftest import alerta_base, processar

pytestmark = pytest.mark.skipif(
    not (os.getenv("COSMOS_ENDPOINT") and os.getenv("COSMOS_KEY")),
    reason="COSMOS_ENDPOINT e COSMOS_KEY não definidos (teste contra o Cosmos real)",
)


@pytest.fixture
def paciente() -> str:
    # Número fora da faixa de pacientes reais do projeto; o pseudônimo é gerado com a chave de teste
    return pseudonimizar_paciente(random.randint(90_000_000, 99_999_999))


@pytest.fixture
def store(paciente):
    armazenamento = CosmosStore(
        os.environ["COSMOS_ENDPOINT"],
        os.environ["COSMOS_KEY"],
        os.getenv("COSMOS_DATABASE", "alertas"),
    )
    yield armazenamento
    # Limpeza: remove o que este teste gravou para o paciente de teste
    for container in (armazenamento._alertas, armazenamento._eventos):
        itens = container.query_items(
            query="SELECT c.id FROM c WHERE c.patient_id = @p",
            parameters=[{"name": "@p", "value": paciente}],
            partition_key=paciente,
        )
        for item in list(itens):
            container.delete_item(item=item["id"], partition_key=paciente)


def _alerta(relogio, paciente, **campos):
    return alerta_base(relogio(), id=uuid.uuid4(), patient_id=paciente,
                       dedup_key=f"{paciente}|sinais_vitais|news2_emergencia", **campos)


def test_salvar_e_obter_devolve_o_mesmo_alerta(store, relogio, paciente):
    alerta = _alerta(relogio, paciente)
    store.salvar(alerta)

    assert store.obter(alerta.id) == alerta


def test_obter_inexistente_devolve_none(store):
    assert store.obter(uuid.uuid4()) is None


def test_buscar_aberto_ignora_reconhecido(store, relogio, paciente):
    alerta = _alerta(relogio, paciente)
    store.salvar(alerta)
    assert store.buscar_aberto(alerta.dedup_key).id == alerta.id

    alerta.status = StatusAlerta.RECONHECIDO
    store.salvar(alerta)

    assert store.buscar_aberto(alerta.dedup_key) is None


def test_listar_filtra_por_status_prioridade_e_paciente(store, relogio, paciente):
    enviado = _alerta(relogio, paciente, status=StatusAlerta.ENVIADO)
    media = _alerta(relogio, paciente, status=StatusAlerta.PENDENTE, priority=Severidade.MEDIA)
    store.salvar(enviado)
    store.salvar(media)

    assert {a.id for a in store.listar(paciente_id=paciente)} == {enviado.id, media.id}
    assert [a.id for a in store.listar(status=StatusAlerta.PENDENTE, paciente_id=paciente)] == [media.id]
    assert [a.id for a in store.listar(prioridade="media", paciente_id=paciente)] == [media.id]


def test_abertos_exclui_reconhecidos(store, relogio, paciente):
    aberto = _alerta(relogio, paciente, status=StatusAlerta.FALHOU)
    fechado = _alerta(relogio, paciente, status=StatusAlerta.RECONHECIDO)
    store.salvar(aberto)
    store.salvar(fechado)

    ids = {a.id for a in store.abertos() if a.patient_id == paciente}

    assert ids == {aberto.id}


def test_pendentes_respeita_o_horario_da_retentativa(store, relogio, paciente):
    agora = relogio()
    pendente = _alerta(relogio, paciente, status=StatusAlerta.PENDENTE)
    adiada = _alerta(relogio, paciente, status=StatusAlerta.FALHOU, retries=1,
                     next_attempt_at=agora + timedelta(minutes=5))
    vencida = _alerta(relogio, paciente, status=StatusAlerta.FALHOU, retries=1,
                      next_attempt_at=agora - timedelta(seconds=1))
    for alerta in (pendente, adiada, vencida):
        store.salvar(alerta)

    ids = {a.id for a in store.pendentes_de_envio(agora, tentativas_max=3) if a.patient_id == paciente}

    assert ids == {pendente.id, vencida.id}


def test_eventos_de_um_alerta_voltam_em_ordem(store, relogio, paciente):
    alerta = _alerta(relogio, paciente)
    store.salvar(alerta)
    store.registrar_evento(novo_evento_de_alerta(alerta, TipoAuditoria.CRIADO, relogio()))
    relogio.avancar(seconds=1)
    store.registrar_evento(novo_evento_de_alerta(alerta, TipoAuditoria.ENVIADO, relogio()))

    eventos = store.eventos(alerta.id)

    assert [e.type for e in eventos] == [TipoAuditoria.CRIADO, TipoAuditoria.ENVIADO]
    assert all(isinstance(e, EventoDeAlerta) for e in eventos)


def test_motor_completo_grava_o_alerta_no_cosmos(store, montar, email, webhook, relogio, paciente):
    servico = montar(store, [email, webhook])

    servico.motor.receber(evento_sintetico(
        Modalidade.SINAIS_VITAIS, "news2_emergencia", severidade=Severidade.ALTA,
        paciente_id=paciente, agora=relogio(), resumo="NEWS2 = 8 (SpO2 89%, FC 128 bpm)",
        features={"spo2": 89, "news2_total": 8}, thresholds={"news2_emergencia": 7},
    ))
    processar(servico)

    [alerta] = store.listar(paciente_id=paciente)
    assert alerta.status is StatusAlerta.ENVIADO
    assert alerta.priority is Severidade.ALTA
    assert len(email.enviados) == 1
    assert [e.type for e in store.eventos(alerta.id)] == [TipoAuditoria.CRIADO, TipoAuditoria.ENVIADO]


def test_duplicata_suprimida_no_cosmos(store, montar, email, webhook, relogio, paciente):
    """Controle de fadiga: duplicata dentro da janela não gera novo alerta no Cosmos."""
    servico = montar(store, [email, webhook])
    achado = evento_sintetico(
        Modalidade.SINAIS_VITAIS, "news2_emergencia", severidade=Severidade.ALTA,
        paciente_id=paciente, agora=relogio(), resumo="NEWS2 = 8 (SpO2 89%, FC 128 bpm)",
        features={"spo2": 89, "news2_total": 8}, thresholds={"news2_emergencia": 7},
    )
    servico.motor.receber(achado)
    processar(servico)

    # Segundo achado idêntico 2 minutos depois (janela de alta é de 5 min)
    relogio.avancar(minutes=2)
    servico.motor.receber(achado)
    processar(servico)

    # No Cosmos DB: exatamente 1 alerta, com 2 ocorrências e e-mail enviado 1 só vez
    alertas = store.listar(paciente_id=paciente)
    assert len(alertas) == 1
    alerta = alertas[0]
    assert alerta.occurrences == 2
    assert len(email.enviados) == 1

    # Trilha de auditoria no container alert_events tem created, sent e suppressed
    tipos_auditoria = [e.type for e in store.eventos(alerta.id)]
    assert TipoAuditoria.CRIADO in tipos_auditoria
    assert TipoAuditoria.ENVIADO in tipos_auditoria
    assert TipoAuditoria.SUPRIMIDO in tipos_auditoria


def test_escalonamento_por_persistencia_no_cosmos(store, montar, email, webhook, relogio, paciente):
    """Controle de fadiga: persistência além do limiar sobe a prioridade e reenvia."""
    servico = montar(store, [email, webhook])
    achado_media = evento_sintetico(
        Modalidade.SINAIS_VITAIS, "news2_resposta_urgente", severidade=Severidade.MEDIA,
        paciente_id=paciente, agora=relogio(),
    )
    servico.motor.receber(achado_media)
    processar(servico)

    alerta = store.listar(paciente_id=paciente)[0]
    assert alerta.priority is Severidade.MEDIA
    assert len(email.enviados) == 1

    # Na política média, escalar_apos_ocorrencias = 5
    for _ in range(4):
        relogio.avancar(minutes=1)
        servico.motor.receber(achado_media)
    processar(servico)

    # Alerta escalonado para ALTA gravado no Cosmos DB
    alerta_escalado = store.obter(alerta.id)
    assert alerta_escalado.priority is Severidade.ALTA
    assert alerta_escalado.escalation_level == 1
    # O escalonamento causou reenvio pelos canais de alta
    assert len(email.enviados) == 2
    tipos_auditoria = [e.type for e in store.eventos(alerta.id)]
    assert TipoAuditoria.ESCALONADO in tipos_auditoria


def test_auditoria_completa_e_reconhecimento_no_cosmos(store, montar, email, webhook, relogio, paciente):
    """Log auditável e confirmação (ACK) persistidos no Cosmos DB."""
    servico = montar(store, [email, webhook])
    servico.motor.receber(evento_sintetico(
        Modalidade.MOVIMENTACAO, "queda", severidade=Severidade.ALTA,
        paciente_id=paciente, agora=relogio(),
    ))
    processar(servico)

    [alerta] = store.listar(paciente_id=paciente)
    assert alerta.status is StatusAlerta.ENVIADO

    # Profissional de saúde reconhece o alerta
    reconhecido = servico.motor.reconhecer(alerta.id, por="Dr. Carlos (CRM 98765)")
    assert reconhecido.status is StatusAlerta.RECONHECIDO
    assert reconhecido.acked_by == "Dr. Carlos (CRM 98765)"

    # Alerta atualizado no Cosmos DB
    alerta_cosmos = store.obter(alerta.id)
    assert alerta_cosmos.status is StatusAlerta.RECONHECIDO
    assert alerta_cosmos.acked_at is not None
    assert alerta_cosmos.acked_by == "Dr. Carlos (CRM 98765)"

    # Trilha completa de auditoria em alert_events
    eventos = store.eventos(alerta.id)
    assert [e.type for e in eventos] == [TipoAuditoria.CRIADO, TipoAuditoria.ENVIADO, TipoAuditoria.RECONHECIDO]
    assert eventos[-1].detail == "por Dr. Carlos (CRM 98765)"
