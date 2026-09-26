"""Contrato de eventos do pipeline multimodal.

Uso típico dentro de um módulo::

    from contratos import Emissor, EventoAchado, Modalidade, Severidade, pseudonimizar_paciente

    def detectar(janela, emissor: Emissor) -> None:   # o módulo recebe o emissor
        emissor.emitir(EventoAchado.criar(
            patient_id=pseudonimizar_paciente(10000032),
            modality=Modalidade.SINAIS_VITAIS,
            event_type="news2_emergencia",
            timestamp=instante_utc,
            score=0.92,
            severity=Severidade.ALTA,
            evidence={"summary": "NEWS2 = 8 (SpO2 89%, FC 128 bpm)", "features": {"news2_total": 8}},
            model_version="anomalias-sinais@0.1.0",
        ))
"""

from .catalogo import SEM_ACHADOS, TIPOS_DE_EVENTO, Modalidade, Severidade, severidade_sugerida, tipos_validos
from .emissor import BarramentoLocal, Emissor, EmissorJsonl, EmissorMemoria, FalhaAssinante, filtro
from .evento import (
    SCHEMA_VERSION,
    TAMANHO_MAX_BYTES,
    Artefato,
    Contexto,
    EventoAchado,
    Evidencia,
    Janela,
    ValidationError,
    agora_utc,
)
from .pseudonimizacao import (
    ChavePseudonimizacaoAusente,
    e_pseudonimo_de_paciente,
    pseudonimizar_internacao,
    pseudonimizar_paciente,
)

__all__ = [
    "SCHEMA_VERSION", "SEM_ACHADOS", "TAMANHO_MAX_BYTES", "TIPOS_DE_EVENTO",
    "Artefato", "BarramentoLocal", "ChavePseudonimizacaoAusente", "Contexto", "Emissor",
    "EmissorJsonl", "EmissorMemoria", "EventoAchado", "Evidencia", "FalhaAssinante", "Janela",
    "Modalidade", "Severidade", "ValidationError", "agora_utc", "e_pseudonimo_de_paciente",
    "filtro", "pseudonimizar_internacao", "pseudonimizar_paciente",
    "severidade_sugerida", "tipos_validos",
]
