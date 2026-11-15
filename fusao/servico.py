"""Fusão em tempo real: assina o barramento e publica ``risco_multimodal``.

A cada achado recebido, recalcula o risco do paciente na janela que termina no
achado mais recente dele (tempo do evento, não do relógio: o replay e o
streaming dão o mesmo resultado). Publica no mesmo barramento, onde o motor de
alertas (US19) o recebe como qualquer outro achado.

Para não inundar o barramento, só republica quando o nível muda ou o score
varia ao menos ``emissao.variacao_minima``. Achados que saíram da janela são
descartados do buffer, então a memória é limitada pela janela.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from contratos import BarramentoLocal, Emissor, EventoAchado, Modalidade, filtro

from .alinhamento import alinhar_paciente, fim_do_achado, instante_mais_recente
from .config import ConfigFusao
from .eventos import evento_de_risco
from .score import Risco, calcular_risco

log = logging.getLogger(__name__)

NOME_ASSINANTE = "fusao-multimodal"


class FusaoMultimodal:
    def __init__(self, config: ConfigFusao, emissor: Emissor | None = None) -> None:
        self.config = config
        self._emissor = emissor
        self._buffer: dict[str, dict] = {}  # patient_id → {event_id: EventoAchado}
        self._ultimo: dict[str, Risco] = {}
        self._publicado: dict[str, Risco] = {}
        self._trava = threading.RLock()

    def assinar(self, barramento: BarramentoLocal) -> None:
        """Recebe todo achado que não é da própria fusão e publica no mesmo barramento."""
        if self._emissor is None:
            self._emissor = barramento
        barramento.assinar(
            NOME_ASSINANTE, self.receber, filtro(exceto_modalidades=[Modalidade.FUSAO])
        )

    def receber(self, evento: EventoAchado) -> Risco | None:
        """Atualiza o risco do paciente. Devolve o risco recalculado, ou ``None`` se ignorado."""
        if evento.modality is Modalidade.FUSAO:
            return None
        with self._trava:
            buffer = self._buffer.setdefault(evento.patient_id, {})
            referencia = instante_mais_recente(buffer.values())
            if referencia is not None and fim_do_achado(evento) < referencia - self.config.janela:
                log.debug("achado %s chegou fora da janela; ignorado", evento.event_id)
                return None
            buffer[evento.event_id] = evento
            risco = self._recalcular(evento.patient_id)
            evento_saida = self._para_publicar(risco)

        if evento_saida is not None and self._emissor is not None:
            self._emissor.emitir(evento_saida)  # fora da trava: o barramento entrega a outros
        return risco

    def risco(self, patient_id: str) -> Risco | None:
        """Último risco calculado do paciente (API)."""
        with self._trava:
            return self._ultimo.get(patient_id)

    def pacientes(self) -> list[Risco]:
        """Último risco de cada paciente, do maior para o menor."""
        with self._trava:
            return sorted(self._ultimo.values(), key=lambda r: r.score, reverse=True)

    def risco_em(self, patient_id: str, ate: datetime) -> Risco | None:
        """Risco recalculado em outro instante, com o que ainda está no buffer."""
        with self._trava:
            eventos = list(self._buffer.get(patient_id, {}).values())
        alinhamento = alinhar_paciente(eventos, patient_id, ate, self.config.janela)
        return None if alinhamento.vazio else calcular_risco(alinhamento, self.config)

    # ----------------------------------------------------------------------- #

    def _recalcular(self, patient_id: str) -> Risco:
        buffer = self._buffer[patient_id]
        ate = instante_mais_recente(buffer.values())
        inicio = ate - self.config.janela
        for event_id in [i for i, e in buffer.items() if fim_do_achado(e) < inicio]:
            del buffer[event_id]
        alinhamento = alinhar_paciente(buffer.values(), patient_id, ate, self.config.janela)
        risco = calcular_risco(alinhamento, self.config)
        self._ultimo[patient_id] = risco
        return risco

    def _para_publicar(self, risco: Risco) -> EventoAchado | None:
        anterior = self._publicado.get(risco.patient_id)
        if anterior is not None and (
            anterior.nivel == risco.nivel
            and anterior.severidade is risco.severidade
            and abs(anterior.score - risco.score) < self.config.variacao_minima
        ):
            return None
        self._publicado[risco.patient_id] = risco
        return evento_de_risco(risco, self.config)
