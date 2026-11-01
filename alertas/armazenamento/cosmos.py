"""Armazenamento no Azure Cosmos DB (NoSQL), o destino de produção da US19.

Dois containers no banco ``alertas`` (criados por ``terraform/cosmos.tf``):

- ``alerts``: estado atual de cada alerta, um documento por alerta (``id`` = UUID do alerta).
- ``alert_events``: trilha de auditoria, um documento por evento.

Os dois usam ``/patient_id`` como chave de partição, então o filtro por paciente é
barato. Consultas sem paciente (aberto por chave de deduplicação, pendentes,
trilha de um alerta) são cross-partition: é aceitável no volume de um hospital
de demonstração, e o free tier cobre.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Mapping

from ..modelos import Alerta, EventoDeAlerta, StatusAlerta
from .memoria import elegivel_para_envio

CONTAINER_ALERTAS = "alerts"
CONTAINER_EVENTOS = "alert_events"


class CosmosStore:
    def __init__(
        self,
        endpoint: str,
        chave: str,
        banco: str = "alertas",
        *,
        container_alertas: Any | None = None,
        container_eventos: Any | None = None,
    ) -> None:
        if container_alertas is None or container_eventos is None:
            from azure.cosmos import CosmosClient

            cliente = CosmosClient(url=endpoint, credential=chave)
            database = cliente.get_database_client(banco)
            container_alertas = database.get_container_client(CONTAINER_ALERTAS)
            container_eventos = database.get_container_client(CONTAINER_EVENTOS)
        self._alertas = container_alertas
        self._eventos = container_eventos

    # ------------------------------------------------------------------ #
    # alertas
    # ------------------------------------------------------------------ #

    def salvar(self, alerta: Alerta) -> None:
        self._alertas.upsert_item(body=alerta.model_dump(mode="json"))

    def obter(self, alerta_id: uuid.UUID) -> Alerta | None:
        documentos = self._alertas.query_items(
            query="SELECT * FROM c WHERE c.id = @id",
            parameters=[{"name": "@id", "value": str(alerta_id)}],
            enable_cross_partition_query=True,
        )
        return next((_alerta(d) for d in documentos), None)

    def buscar_aberto(self, chave: str) -> Alerta | None:
        documentos = self._alertas.query_items(
            query=(
                "SELECT TOP 1 * FROM c WHERE c.dedup_key = @chave AND c.status != @ack "
                "ORDER BY c.alert_created_at DESC"
            ),
            parameters=[
                {"name": "@chave", "value": chave},
                {"name": "@ack", "value": StatusAlerta.RECONHECIDO.value},
            ],
            enable_cross_partition_query=True,
        )
        return next((_alerta(d) for d in documentos), None)

    def abertos(self) -> list[Alerta]:
        documentos = self._alertas.query_items(
            query="SELECT * FROM c WHERE c.status != @ack",
            parameters=[{"name": "@ack", "value": StatusAlerta.RECONHECIDO.value}],
            enable_cross_partition_query=True,
        )
        return [_alerta(d) for d in documentos]

    def pendentes_de_envio(self, agora: datetime, tentativas_max: int) -> list[Alerta]:
        # O filtro por horário de retentativa é feito em Python, com a mesma regra da memória.
        documentos = self._alertas.query_items(
            query="SELECT * FROM c WHERE c.status IN (@pendente, @falhou)",
            parameters=[
                {"name": "@pendente", "value": StatusAlerta.PENDENTE.value},
                {"name": "@falhou", "value": StatusAlerta.FALHOU.value},
            ],
            enable_cross_partition_query=True,
        )
        return [a for a in (_alerta(d) for d in documentos) if elegivel_para_envio(a, agora, tentativas_max)]

    def listar(
        self,
        *,
        status: StatusAlerta | None = None,
        prioridade: str | None = None,
        paciente_id: str | None = None,
        limite: int = 100,
    ) -> list[Alerta]:
        filtros = ["1 = 1"]
        parametros: list[dict[str, Any]] = [{"name": "@limite", "value": limite}]
        if status is not None:
            filtros.append("c.status = @status")
            parametros.append({"name": "@status", "value": status.value})
        if prioridade is not None:
            filtros.append("c.priority = @prioridade")
            parametros.append({"name": "@prioridade", "value": prioridade})
        if paciente_id is not None:
            filtros.append("c.patient_id = @paciente")
            parametros.append({"name": "@paciente", "value": paciente_id})
        documentos = self._alertas.query_items(
            query=(
                f"SELECT TOP @limite * FROM c WHERE {' AND '.join(filtros)} "
                "ORDER BY c.alert_created_at DESC"
            ),
            parameters=parametros,
            enable_cross_partition_query=True,
        )
        return [_alerta(d) for d in documentos]

    # ------------------------------------------------------------------ #
    # auditoria
    # ------------------------------------------------------------------ #

    def registrar_evento(self, evento: EventoDeAlerta) -> None:
        self._eventos.upsert_item(body=evento.model_dump(mode="json"))

    def eventos(self, alerta_id: uuid.UUID) -> list[EventoDeAlerta]:
        documentos = self._eventos.query_items(
            query="SELECT * FROM c WHERE c.alert_id = @id ORDER BY c.at ASC",
            parameters=[{"name": "@id", "value": str(alerta_id)}],
            enable_cross_partition_query=True,
        )
        return [EventoDeAlerta.model_validate(_limpo(d)) for d in documentos]


def _alerta(documento: Mapping[str, Any]) -> Alerta:
    return Alerta.model_validate(_limpo(documento))


def _limpo(documento: Mapping[str, Any]) -> dict[str, Any]:
    """Tira as propriedades de sistema do Cosmos (``_rid``, ``_etag``, ``_ts``…), que o modelo não tem."""
    return {chave: valor for chave, valor in documento.items() if not chave.startswith("_")}
