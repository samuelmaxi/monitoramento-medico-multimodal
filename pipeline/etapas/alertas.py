"""Etapa de avaliação e despacho de alertas clínicos (DoD 1, US19)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from alertas import ConfigAlertas, ServicoAlertas, construir
from contratos import EventoAchado
from pipeline.etapas.base import ContextoPipeline

log = logging.getLogger("pipeline.etapas.alertas")


class EtapaAlertas:
    """Processa a outbox de alertas clínicos gerados a partir dos achados e da fusão."""

    nome = "alertas"

    def executar(self, contexto: ContextoPipeline) -> list[EventoAchado]:
        contexto.logger.iniciar_etapa(self.nome)
        detalhes: dict[str, Any] = {}

        servico: ServicoAlertas = contexto.dados.get("alertas")

        if servico is not None:
            # Processa pendências na fila de despacho
            try:
                asyncio.run(servico.processar_pendentes())
            except RuntimeError:
                # Se já houver um event loop ativo no processo
                loop = asyncio.get_event_loop()
                loop.run_until_complete(servico.processar_pendentes())

            alertas_gerados = servico.armazenamento.listar()
            detalhes["total_alertas_gerados"] = len(alertas_gerados)

            contagem_prioridade: dict[str, int] = {}
            lista_resumos = []
            for a in alertas_gerados:
                prio = a.priority.value
                contagem_prioridade[prio] = contagem_prioridade.get(prio, 0) + 1
                lista_resumos.append({
                    "id": a.id,
                    "prioridade": prio,
                    "leito": a.bed_id,
                    "tipo": a.event_type,
                    "resumo": a.summary,
                    "status": a.status.value,
                })

            detalhes["por_prioridade"] = contagem_prioridade
            detalhes["alertas"] = lista_resumos

        contexto.logger.concluir_etapa(
            eventos_emitidos=detalhes.get("total_alertas_gerados", 0),
            status="sucesso",
            detalhes=detalhes,
        )
        return []

