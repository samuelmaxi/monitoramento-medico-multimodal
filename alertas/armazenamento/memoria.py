"""Armazenamento em memória. Usado nos testes e como base do JSONL."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime

from ..modelos import Alerta, EventoDeAlerta, StatusAlerta


class MemoriaStore:
    """Guarda cópias: quem lê ou altera um alerta precisa gravá-lo de volta com ``salvar``."""

    def __init__(self) -> None:
        self._alertas: dict[uuid.UUID, Alerta] = {}
        self._eventos: list[EventoDeAlerta] = []
        self._trava = threading.RLock()

    def salvar(self, alerta: Alerta) -> None:
        with self._trava:
            self._alertas[alerta.id] = alerta.model_copy(deep=True)

    def obter(self, alerta_id: uuid.UUID) -> Alerta | None:
        with self._trava:
            alerta = self._alertas.get(alerta_id)
            return alerta.model_copy(deep=True) if alerta else None

    def buscar_aberto(self, chave: str) -> Alerta | None:
        with self._trava:
            candidatos = [
                a for a in self._alertas.values()
                if a.dedup_key == chave and a.status is not StatusAlerta.RECONHECIDO
            ]
            if not candidatos:
                return None
            return max(candidatos, key=lambda a: a.alert_created_at).model_copy(deep=True)

    def abertos(self) -> list[Alerta]:
        with self._trava:
            return [a.model_copy(deep=True) for a in self._alertas.values()
                    if a.status is not StatusAlerta.RECONHECIDO]

    def pendentes_de_envio(self, agora: datetime, tentativas_max: int) -> list[Alerta]:
        with self._trava:
            elegiveis = [a for a in self._alertas.values() if _elegivel(a, agora, tentativas_max)]
            return [a.model_copy(deep=True) for a in elegiveis]

    def listar(
        self,
        *,
        status: StatusAlerta | None = None,
        prioridade: str | None = None,
        paciente_id: str | None = None,
        limite: int = 100,
    ) -> list[Alerta]:
        with self._trava:
            filtrados = [
                a for a in self._alertas.values()
                if (status is None or a.status is status)
                and (prioridade is None or a.priority.value == prioridade)
                and (paciente_id is None or a.patient_id == paciente_id)
            ]
        filtrados.sort(key=lambda a: a.alert_created_at, reverse=True)
        return [a.model_copy(deep=True) for a in filtrados[:limite]]

    def registrar_evento(self, evento: EventoDeAlerta) -> None:
        with self._trava:
            self._eventos.append(evento.model_copy(deep=True))

    def eventos(self, alerta_id: uuid.UUID) -> list[EventoDeAlerta]:
        with self._trava:
            return [e.model_copy(deep=True) for e in self._eventos if e.alert_id == alerta_id]


def _elegivel(alerta: Alerta, agora: datetime, tentativas_max: int) -> bool:
    if alerta.status is StatusAlerta.PENDENTE:
        return True
    if alerta.status is not StatusAlerta.FALHOU or alerta.retries >= tentativas_max:
        return False
    return alerta.next_attempt_at is None or alerta.next_attempt_at <= agora
