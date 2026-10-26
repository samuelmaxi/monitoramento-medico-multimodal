"""Montagem dos eventos da US13 no contrato ``EventoAchado`` (US04).

- ``termo_critico``: um evento por termo crítico afirmado na consulta. Se o termo
  aparece várias vezes, fica a ocorrência de maior severidade e confiança, com a
  contagem em ``ocorrencias`` (evita disparar o mesmo alerta N vezes).
- ``sentimento_negativo``: um por consulta predominantemente negativa.
- ``sem_achados``: consulta analisada sem nenhum dos dois, para a fusão (US18)
  distinguir "texto presente e normal" de "texto ausente".
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from contratos import EventoAchado, Modalidade, Severidade, e_pseudonimo_de_paciente, pseudonimizar_paciente
from contratos.catalogo import SEM_ACHADOS
from contratos.evento import PADRAO_URI_ARTEFATO

from .config import TextConfig
from .transcript import Segment, Transcript

TERMO_CRITICO = "termo_critico"
SENTIMENTO_NEGATIVO = "sentimento_negativo"

_MAX_FEATURE = 200


def cortar(texto: Optional[str], limite: int = _MAX_FEATURE) -> Optional[str]:
    if texto is None:
        return None
    texto = " ".join(texto.split())
    return texto if len(texto) <= limite else texto[: limite - 1].rstrip() + "…"


def patient_pseudonym(identificador: str) -> str:
    """Pseudônimo do contrato; um ID que já é pseudônimo passa direto."""
    return identificador if e_pseudonimo_de_paciente(identificador) else pseudonimizar_paciente(identificador)


def relative_uri(caminho: Optional[Path]) -> Optional[str]:
    """Caminho relativo à raiz do repositório, como o contrato exige (ou ``None``)."""
    if caminho is None:
        return None
    try:
        relativo = Path(os.path.relpath(Path(caminho).resolve(), Path.cwd().resolve())).as_posix()
    except ValueError:  # outro drive no Windows
        return None
    return relativo if re.fullmatch(PADRAO_URI_ARTEFATO, relativo) else None


@dataclass(frozen=True)
class CriticalFinding:
    """Ocorrência de termo crítico que passou pelo filtro de asserção."""

    term_name: str
    severity_list: Severidade
    severity: Severidade
    downgraded: bool
    segment: Segment
    offset_ms: int
    entity_text: str
    sentence: str
    category: str
    normalized_text: Optional[str]
    confidence: float
    certainty: Optional[str]
    conditionality: Optional[str]
    association: Optional[str]
    temporality: Optional[str]
    temporality_source: Optional[str]
    matched_by: str
    doctor_context: Optional[str]


class EventFactory:
    def __init__(self, transcript: Transcript, config: TextConfig, models: dict[str, str]) -> None:
        self._t = transcript
        self._config = config
        self._models = models
        self._patient_id = patient_pseudonym(transcript.patient_id)
        self._uri = relative_uri(transcript.source)

    @property
    def patient_id(self) -> str:
        return self._patient_id

    def _artefato(self, inicio_ms: int, fim_ms: int) -> list[dict[str, Any]]:
        if self._uri is None:
            return []
        return [{
            "kind": "transcricao", "uri": self._uri, "media_type": "application/json",
            "start_ms": inicio_ms, "end_ms": fim_ms,
        }]

    def _janela(self, inicio_ms: int, fim_ms: int) -> dict[str, datetime]:
        return {"start": self._t.instant(inicio_ms), "end": self._t.instant(fim_ms)}

    def _base(self, event_type: str, timestamp_ms: int) -> dict[str, Any]:
        return {
            "patient_id": self._patient_id,
            "modality": Modalidade.TEXTO,
            "event_type": event_type,
            "timestamp": self._t.instant(timestamp_ms),
            "model_version": self._config.model_version,
            "context": {"source_id": self._t.audio_id},
            "is_synthetic": self._t.is_synthetic,
        }

    def critical_term(self, achado: CriticalFinding, ocorrencias: int) -> EventoAchado:
        seg = achado.segment
        if achado.downgraded:
            situacao = (f"histórico passado; severidade rebaixada de {achado.severity_list.value} "
                        f"para {achado.severity.value}")
        else:
            situacao = "afirmado, sem negação"
        resumo = f"Paciente relata {achado.term_name}: “{cortar(achado.entity_text, 80)}” ({situacao})"
        return EventoAchado.criar(
            **self._base(TERMO_CRITICO, achado.offset_ms),
            window=self._janela(seg.offset_ms, seg.end_ms),
            score=round(achado.confidence, 4),
            severity=achado.severity,
            evidence={
                "summary": resumo,
                "features": {
                    "termo": achado.term_name,
                    "texto": cortar(achado.entity_text),
                    "trecho": cortar(achado.sentence),
                    "categoria": achado.category,
                    "nome_normalizado": cortar(achado.normalized_text),
                    "confianca": round(achado.confidence, 4),
                    "certeza": achado.certainty,
                    "condicionalidade": achado.conditionality,
                    "associacao": achado.association,
                    "temporalidade": achado.temporality,
                    "temporalidade_fonte": achado.temporality_source,
                    "severidade_lista": achado.severity_list.value,
                    "rebaixada": achado.downgraded,
                    "casou_por": cortar(achado.matched_by),
                    "falante": "paciente",
                    "segmento": seg.index,
                    "offset_ms": achado.offset_ms,
                    "ocorrencias": ocorrencias,
                    "pergunta_medico": cortar(achado.doctor_context),
                    "lista_termos_versao": self._config.critical_terms.version,
                },
                "models": self._models,
                "artifacts": self._artefato(seg.offset_ms, seg.end_ms),
            },
        )

    def negative_sentiment(self, resumo: dict[str, Any], pior: dict[str, Any], inicio_ms: int, fim_ms: int) -> EventoAchado:
        cfg = self._config.sentiment
        pct = resumo["pct_negativas"]
        return EventoAchado.criar(
            **self._base(SENTIMENTO_NEGATIVO, pior["offset_ms"]),
            window=self._janela(inicio_ms, fim_ms),
            score=round(resumo["score_medio"]["negative"], 4),
            severity=cfg.severity,
            evidence={
                "summary": (f"Sentimento predominantemente negativo nas falas do paciente: "
                            f"{resumo['sentencas_negativas']} de {resumo['total_sentencas']} sentenças "
                            f"({pct:.0%}) com score negativo ≥ {cfg.negative_threshold}"),
                "features": {
                    "total_sentencas": resumo["total_sentencas"],
                    "sentencas_negativas": resumo["sentencas_negativas"],
                    "pct_negativas": round(pct, 4),
                    "score_medio_positivo": round(resumo["score_medio"]["positive"], 4),
                    "score_medio_neutro": round(resumo["score_medio"]["neutral"], 4),
                    "score_medio_negativo": round(resumo["score_medio"]["negative"], 4),
                    "trecho": cortar(pior["texto"]),
                    "trecho_score_negativo": round(pior["negative"], 4),
                    "offset_ms": pior["offset_ms"],
                    "falante": "paciente",
                },
                "thresholds": {
                    "limiar_negativo": cfg.negative_threshold,
                    "pct_minimo_negativas": cfg.min_negative_ratio,
                },
                "models": self._models,
                "artifacts": self._artefato(pior["segmento_inicio_ms"], pior["segmento_fim_ms"]),
            },
        )

    def no_findings(self, features: dict[str, Any], inicio_ms: int, fim_ms: int) -> EventoAchado:
        return EventoAchado.criar(
            **self._base(SEM_ACHADOS, inicio_ms),
            window=self._janela(inicio_ms, fim_ms),
            score=0.0,
            severity=Severidade.INFO,
            evidence={
                "summary": "Transcrição analisada sem termos críticos afirmados nem sentimento predominantemente negativo",
                "features": features,
                "thresholds": {
                    "limiar_negativo": self._config.sentiment.negative_threshold,
                    "pct_minimo_negativas": self._config.sentiment.min_negative_ratio,
                },
                "models": self._models,
                "artifacts": self._artefato(inicio_ms, fim_ms),
            },
        )
