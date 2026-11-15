"""Etapa de prescrições: regras de evolução de prescrição e ISMP da US16 (DoD 1)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from contratos import EventoAchado, agora_utc
from pipeline.etapas.base import ContextoPipeline
from prescricoes.config import carregar_config
from prescricoes.entrada import criar_prescricao
from prescricoes.modelos import Internacao, Prescricao
from prescricoes.run import eventos_de

log = logging.getLogger("pipeline.etapas.prescricoes")


def gerar_prescricoes_demo(paciente: str, internacao: str, agora: datetime) -> list[Prescricao]:
    """Evolução de prescrição do cenário de deterioração respiratória.

    Morfina IV de 2 para 8 mg há 2 h (mudança abrupta de dose, opioide de alta vigilância)
    e heparina SC iniciada há 1 h (introdução de anticoagulante).
    """

    def ordem(ordem_id, medicamento, dose, unidade, via, frequencia, inicio, fim, doses_24h=None):
        return criar_prescricao(
            ordem_id=ordem_id, paciente=paciente, internacao=internacao,
            medicamento=medicamento, dose=dose, unidade=unidade, via=via,
            frequencia=frequencia, inicio=inicio, fim=fim, doses_24h=doses_24h, sintetica=True,
        )

    # Sem `fim` e sem alta a ordem vale só no instante do início. O `fim` das ordens ativas é
    # "agora": ele vira o fim da janela do achado, e a fusão descarta o que é mais antigo que
    # a janela contada desse instante, então um fim no futuro apagaria os outros achados.
    fim_plantao = agora
    return [
        ordem("demo-1", "Morphine Sulfate", 2, "mg", "IV PUSH", "Q4H",
              agora - timedelta(hours=30), agora - timedelta(hours=2), 6),
        ordem("demo-2", "Morphine Sulfate", 8, "mg", "IV PUSH", "Q4H",
              agora - timedelta(hours=2), fim_plantao, 6),
        ordem("demo-3", "Heparin", 5000, "UNIT", "SC", "Q8H",
              agora - timedelta(hours=1), fim_plantao, 3),
    ]


class EtapaPrescricoes:
    """Detecta anomalias na evolução de prescrições com as regras da US16."""

    nome = "prescricoes"

    def executar(self, contexto: ContextoPipeline) -> list[EventoAchado]:
        contexto.logger.iniciar_etapa(self.nome)
        detalhes: dict[str, Any] = {}
        agora = agora_utc()

        cfg = carregar_config()
        internacao = Internacao(
            id=f"internacao-{contexto.leito_id.lower()}",
            paciente=contexto.paciente_id,
            admissao=agora - timedelta(days=2),
            alta=None,
        )
        prescricoes = gerar_prescricoes_demo(contexto.paciente_id, internacao.id, agora)
        anomalias, achados = eventos_de(
            prescricoes,
            {internacao.id: internacao},
            cfg,
            chave=contexto.config.chave_pseudonimizacao,
        )

        eventos: list[EventoAchado] = []
        for evento in achados:
            if evento.context is not None:
                contexto_leito = evento.context.model_copy(update={"bed_id": contexto.leito_id})
                evento = evento.model_copy(update={"context": contexto_leito})
            contexto.barramento.emitir(evento)
            eventos.append(evento)

        detalhes["fonte"] = "cenário de demonstração (regras da US16, versão " + cfg.versao + ")"
        detalhes["prescricoes_analisadas"] = len(prescricoes)
        detalhes["anomalias"] = [f"{a.event_type}:{a.regra}" for a in anomalias]
        contexto.logger.concluir_etapa(
            eventos_emitidos=len(eventos), status="sucesso", detalhes=detalhes
        )
        return eventos
