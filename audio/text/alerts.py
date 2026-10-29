"""Ponte da US13 para o motor de alertas (US14/US19).

>>> STUB <<<  O motor de alertas ainda não existe no repositório. A interface de
integração definida na US04 é o ``BarramentoLocal``: o motor assina o barramento
com um filtro e recebe os eventos. Este módulo registra um assinante provisório
que só escreve no log os eventos de severidade alta da modalidade ``texto``.

Quando a US19 entrar, troque ``publicar_alerta`` pelo ``receber`` do motor (ou
apague este módulo e deixe o próprio motor assinar o barramento). Os detectores
não mudam: continuam só publicando no barramento.
"""

from __future__ import annotations

import logging

from contratos import BarramentoLocal, EventoAchado, Modalidade, Severidade, filtro

log = logging.getLogger(__name__)

NOME_ASSINANTE = "alertas-stub-us13"
SEVERIDADE_MINIMA = Severidade.ALTA


def publicar_alerta(evento: EventoAchado) -> None:
    """STUB: substituir pelo envio real do motor de alertas (US19)."""
    log.warning("[STUB alertas US14/US19] %s %s severidade=%s paciente=%s: %s",
                evento.event_type, evento.event_id, evento.severity.value,
                evento.patient_id, evento.evidence.summary)


def assinar_alertas(barramento: BarramentoLocal) -> None:
    """Registra o stub para receber os eventos de texto com severidade alta."""
    barramento.assinar(
        NOME_ASSINANTE,
        publicar_alerta,
        filtro(modalidades=[Modalidade.TEXTO], severidade_minima=SEVERIDADE_MINIMA),
    )
