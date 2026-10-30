from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alertas import Alerta, Despachante, MotorAlertas, ServicoAlertas, StatusAlerta, carregar_regras
from alertas.armazenamento import AlertStore, MemoriaStore
from alertas.notificadores import NotificadorFake

RAIZ = Path(__file__).resolve().parents[2]
CHAVE_TESTE = "chave-de-teste-com-mais-de-32-bytes-000000"


@pytest.fixture(autouse=True)
def _chave(monkeypatch):
    monkeypatch.setenv("PSEUDONYM_KEY", CHAVE_TESTE)


class Relogio:
    """Relógio injetável: o teste avança o tempo sem esperar de verdade."""

    def __init__(self, inicio: datetime) -> None:
        self.agora = inicio

    def __call__(self) -> datetime:
        return self.agora

    def avancar(self, **intervalo) -> None:
        self.agora += timedelta(**intervalo)


@pytest.fixture
def relogio() -> Relogio:
    return Relogio(datetime(2026, 9, 26, 17, 0, tzinfo=UTC))


@pytest.fixture
def regras():
    return carregar_regras(RAIZ / "config" / "alertas_regras.yaml")


@pytest.fixture
def montar(regras, relogio):
    """Monta o serviço inteiro (motor, despachante, timer) com o armazenamento e canais dados."""

    def _montar(
        armazenamento: AlertStore | None = None,
        notificadores=(),
        *,
        tentativas_max: int = 3,
    ) -> ServicoAlertas:
        if armazenamento is None:
            armazenamento = MemoriaStore()
        despachante = Despachante(
            {n.canal: n for n in notificadores},
            armazenamento,
            relogio=relogio,
            tentativas_max=tentativas_max,
            backoff_base_s=5.0,
        )
        motor = MotorAlertas(armazenamento, regras, despachante.acordar, relogio=relogio)
        return ServicoAlertas(motor, despachante, segredo_ack="segredo-de-teste")

    return _montar


@pytest.fixture
def email() -> NotificadorFake:
    return NotificadorFake("email")


@pytest.fixture
def webhook() -> NotificadorFake:
    return NotificadorFake("webhook")


def alerta_base(agora: datetime, **campos) -> Alerta:
    """Alerta mínimo válido, para testar funções puras sobre o estado."""
    dados = {
        "id": "00000000-0000-4000-8000-000000000001",
        "dedup_key": "pt_x|sinais_vitais|news2_emergencia",
        "patient_id": "pt_" + "0" * 24,
        "status": StatusAlerta.ENVIADO,
        "priority": "alta",
        "modalities": ["sinais_vitais"],
        "event_type": "news2_emergencia",
        "summary": "NEWS2 = 8",
        "suggested_action": "Avaliar",
        "rule_origin": "regra:sinais_vitais/news2_emergencia",
        "escalation_mark_at": agora,
        "last_seen_at": agora,
        "event_at": agora,
        "detected_at": agora,
        "alert_created_at": agora,
        "latency_base": "detected_at",
    }
    dados.update(campos)
    return Alerta(**dados)


def processar(servico: ServicoAlertas) -> None:
    import asyncio

    asyncio.run(servico.processar_pendentes())
