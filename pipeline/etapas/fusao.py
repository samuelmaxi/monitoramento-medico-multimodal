"""Etapa de consolidação e avaliação da Fusão Multimodal (DoD 1, US18)."""

from __future__ import annotations

import logging
from typing import Any

from contratos import EventoAchado, Modalidade
from fusao import FusaoMultimodal
from pipeline.etapas.base import ContextoPipeline

log = logging.getLogger("pipeline.etapas.fusao")


class EtapaFusao:
    """Consolida o score de risco cross-modal derivado dos achados multimodais."""

    nome = "fusao"

    def executar(self, contexto: ContextoPipeline) -> list[EventoAchado]:
        contexto.logger.iniciar_etapa(self.nome)
        detalhes: dict[str, Any] = {}

        fusao: FusaoMultimodal = contexto.dados.get("fusao")
        eventos_fusao: list[EventoAchado] = []

        if fusao is not None:
            risco = fusao.risco(contexto.paciente_id)
            detalhes["score_risco_final"] = round(risco.score, 4)
            detalhes["nivel_risco"] = risco.nivel
            detalhes["severidade"] = risco.severidade.value
            detalhes["presentes"] = [m.value for m in risco.presentes]
            detalhes["ausentes"] = [m.value for m in risco.ausentes]
            detalhes["contribuicoes"] = {
                k: round(v, 4) for k, v in risco.contribuicoes.items()
            }
            if risco.concordancia:
                detalhes["concordancia_eixo"] = risco.concordancia.eixo
                detalhes["concordancia_modalidades"] = [
                    m.value for m in risco.concordancia.modalidades
                ]
                detalhes["concordancia_bonus"] = round(risco.concordancia.contribuicao, 4)

            # Busca eventos de risco_multimodal gravados no barramento
            if contexto.barramento.caminho and contexto.barramento.caminho.is_file():
                linhas = contexto.barramento.caminho.read_text(encoding="utf-8").splitlines()
                for linha in linhas:
                    if f'"modality":"{Modalidade.FUSAO.value}"' in linha:
                        try:
                            ev = EventoAchado.model_validate_json(linha)
                            if ev.patient_id == contexto.paciente_id:
                                eventos_fusao.append(ev)
                        except Exception:  # noqa: BLE001
                            pass

        detalhes["total_eventos_risco_publicados"] = len(eventos_fusao)
        contexto.logger.concluir_etapa(
            eventos_emitidos=len(eventos_fusao),
            status="sucesso",
            detalhes=detalhes,
        )
        return eventos_fusao
