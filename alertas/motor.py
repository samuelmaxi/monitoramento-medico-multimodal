"""Motor de alertas: decide se um achado vira alerta, agrega duplicatas e escalona.

Assina o ``BarramentoLocal``, que entrega na thread de quem publicou. Por isso o
estado é protegido por trava. O envio não acontece aqui: o motor só grava o alerta
(outbox) e chama ``acordar`` para o despachante.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import datetime

from contratos import EventoAchado, agora_utc

from .armazenamento import AlertStore, gravar
from .dedup import chave_dedup, motivo_escalonamento, proxima_prioridade
from .modelos import Alerta, StatusAlerta, TipoAuditoria
from .regras import Decisao, Regras, avaliar

log = logging.getLogger(__name__)

MAX_EVENTOS_ORIGEM = 200  # mesmo limite de related_event_ids no contrato


class MotorAlertas:
    def __init__(
        self,
        armazenamento: AlertStore,
        regras: Regras,
        acordar: Callable[[uuid.UUID], None],
        *,
        relogio: Callable[[], datetime] = agora_utc,
    ) -> None:
        self._armazenamento = armazenamento
        self._regras = regras
        self._acordar = acordar
        self._relogio = relogio
        self._trava = threading.RLock()

    @property
    def armazenamento(self) -> AlertStore:
        return self._armazenamento

    def receber(self, evento: EventoAchado) -> None:
        """Assinante do barramento. Não lança exceção por achado sem alerta."""
        decisao = avaliar(evento, self._regras)
        if decisao is None:
            return

        with self._trava:
            agora = self._relogio()
            aberto = self._armazenamento.buscar_aberto(chave_dedup(evento))
            if aberto is not None and agora - aberto.last_seen_at <= decisao.janela_dedup:
                alerta = aberto
                precisa_enviar = self._agregar(alerta, evento, agora)
            else:
                alerta = self._criar(evento, decisao, agora)
                gravar(self._armazenamento, alerta, TipoAuditoria.CRIADO, agora, decisao.origem)
                precisa_enviar = True

        if precisa_enviar:
            self._acordar(alerta.id)

    def varrer(self) -> list[uuid.UUID]:
        """Escalona alertas que persistiram sem eventos novos. Chamado pelo timer."""
        escalados: list[uuid.UUID] = []
        with self._trava:
            agora = self._relogio()
            for alerta in self._armazenamento.abertos():
                if self._escalar_se_preciso(alerta, agora):
                    escalados.append(alerta.id)
        for alerta_id in escalados:
            self._acordar(alerta_id)
        return escalados

    def reconhecer(self, alerta_id: uuid.UUID, por: str) -> Alerta | None:
        with self._trava:
            alerta = self._armazenamento.obter(alerta_id)
            if alerta is None:
                return None
            if alerta.status is StatusAlerta.RECONHECIDO:
                return alerta
            agora = self._relogio()
            alerta.status = StatusAlerta.RECONHECIDO
            alerta.acked_at = agora
            alerta.acked_by = por
            alerta.next_attempt_at = None
            gravar(self._armazenamento, alerta, TipoAuditoria.RECONHECIDO, agora, f"por {por}")
            return alerta

    def _agregar(self, alerta: Alerta, evento: EventoAchado, agora: datetime) -> bool:
        """Soma o achado ao alerta aberto. Devolve True se o alerta precisa ser reenviado."""
        alerta.occurrences += 1
        alerta.occurrences_since_escalation += 1
        alerta.last_seen_at = agora
        if len(alerta.source_event_ids) < MAX_EVENTOS_ORIGEM:
            alerta.source_event_ids.append(evento.event_id)

        if self._escalar_se_preciso(alerta, agora):
            return True
        gravar(
            self._armazenamento, alerta, TipoAuditoria.SUPRIMIDO, agora,
            f"ocorrência {alerta.occurrences} de {evento.event_type} agregada à janela",
        )
        return False

    def _escalar_se_preciso(self, alerta: Alerta, agora: datetime) -> bool:
        politica = self._regras.politica(alerta.priority)
        motivo = motivo_escalonamento(alerta, politica, agora)
        if motivo is None:
            return False

        anterior = alerta.priority
        alerta.priority = proxima_prioridade(anterior)
        alerta.pending_channels = list(self._regras.politica(alerta.priority).canais)
        alerta.status = StatusAlerta.PENDENTE
        alerta.escalation_level += 1
        alerta.escalation_mark_at = agora
        alerta.occurrences_since_escalation = 0
        alerta.retries = 0
        alerta.next_attempt_at = None
        gravar(
            self._armazenamento, alerta, TipoAuditoria.ESCALONADO, agora,
            f"{anterior.value} → {alerta.priority.value} por {motivo}",
        )
        log.info("alerta %s escalonado: %s → %s", alerta.id, anterior.value, alerta.priority.value)
        return True

    def _criar(self, evento: EventoAchado, decisao: Decisao, agora: datetime) -> Alerta:
        contexto = evento.context
        return Alerta(
            id=uuid.uuid4(),
            dedup_key=chave_dedup(evento),
            patient_id=evento.patient_id,
            status=StatusAlerta.PENDENTE,
            priority=decisao.prioridade,
            modalities=[evento.modality.value],
            event_type=evento.event_type,
            bed_id=contexto.bed_id if contexto else None,
            summary=evento.evidence.summary,
            suggested_action=decisao.acao_sugerida,
            evidence={
                "summary": evento.evidence.summary,
                "features": dict(evento.evidence.features),
                "thresholds": dict(evento.evidence.thresholds),
            },
            rule_origin=decisao.origem,
            is_synthetic=evento.is_synthetic,
            occurrences=1,
            occurrences_since_escalation=1,
            escalation_mark_at=agora,
            last_seen_at=agora,
            source_event_ids=[evento.event_id],
            event_at=evento.timestamp,
            ingested_at=evento.ingested_at,
            detected_at=evento.detected_at,
            alert_created_at=agora,
            latency_base="ingested_at" if evento.ingested_at else "detected_at",
            pending_channels=list(decisao.canais),
        )
