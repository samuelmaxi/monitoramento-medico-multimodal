"""Etapa de movimentação: imobilidade e quedas pelas regras da US17 (DoD 1)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from contratos import EventoAchado, agora_utc
from movimentacao.config import carregar_config
from movimentacao.eventos import montar_evento, montar_sem_achados
from movimentacao.modelos import Observacao, Posicao
from movimentacao.regras import detectar
from pipeline.etapas.base import ContextoPipeline

log = logging.getLogger("pipeline.etapas.movimentacao")

INTERVALO_REGISTRO_MIN = 30
IMOBILIDADE_DEMO_MIN = 190


def gerar_observacoes_demo(agora: datetime) -> list[Observacao]:
    """Registro de enfermagem: paciente em supino, sem reposicionamento, por 190 min."""
    return [
        Observacao(agora - timedelta(minutes=m), Posicao.SUPINO, "enfermagem")
        for m in range(IMOBILIDADE_DEMO_MIN, -1, -INTERVALO_REGISTRO_MIN)
    ]


class EtapaMovimentacao:
    """Detecta imobilidade prolongada e risco de lesão por pressão (regras da US17)."""

    nome = "movimentacao"

    def executar(self, contexto: ContextoPipeline) -> list[EventoAchado]:
        contexto.logger.iniciar_etapa(self.nome)
        detalhes: dict[str, Any] = {}
        agora = agora_utc()

        cfg = carregar_config()
        serie = gerar_observacoes_demo(agora)
        anomalias = detectar(serie, cfg, ate=agora)
        ids = {
            "paciente": contexto.paciente_id,
            "bed_id": contexto.leito_id,
            "source_id": f"leito-{contexto.leito_id.lower()}",
            "sintetico": True,
        }
        eventos = [montar_evento(a, cfg, **ids) for a in anomalias]
        if not eventos:
            eventos = [montar_sem_achados(serie, cfg, **ids)]
        for evento in eventos:
            contexto.barramento.emitir(evento)

        detalhes["fonte"] = f"cenário de demonstração (regras da US17, versão {cfg.versao})"
        detalhes["observacoes_analisadas"] = len(serie)
        detalhes["anomalias"] = [a.event_type for a in anomalias]
        contexto.logger.concluir_etapa(
            eventos_emitidos=len(eventos), status="sucesso", detalhes=detalhes
        )
        return eventos
