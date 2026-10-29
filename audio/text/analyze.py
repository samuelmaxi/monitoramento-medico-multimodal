"""Análise de uma transcrição: termos críticos e sentimento nas falas do paciente.

Uso dentro do pipeline (o módulo recebe o emissor, como manda o contrato)::

    resultado = analyze_transcript(transcricao, config, cliente, emissor=barramento)

1. Separa as falas pela diarização da US11. Só as do paciente vão para a Azure;
   a fala do médico ("você sente falta de ar?") nunca gera achado e fica como
   contexto (``pergunta_medico`` na evidência).
2. Divide falas longas por sentença (limite de 5.120 caracteres por documento).
3. Text Analytics for health (``pt``) → filtro de asserção → lista de termos críticos.
4. Sentimento (``pt-BR``) por sentença → resumo da consulta.
5. Monta os eventos (US04) e publica no emissor.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from contratos import Emissor, EventoAchado

from .assertions import downgrade, evaluate_assertion, resolve_temporality
from .config import TextConfig
from .events import CriticalFinding, EventFactory, cortar, relative_uri
from .language_client import API_VERSION, OP_HEALTH, OP_SENTIMENT, LanguageClient
from .segmentation import Chunk, chunk_text, sentence_at
from .terms import TermMatcher
from .transcript import Segment, Transcript

log = logging.getLogger(__name__)

DECISAO_TERMO = "termo_critico"
DECISAO_DESCARTADA = "descartada"
DECISAO_SEM_TERMO = "sem_termo_critico"


@dataclass
class AnalysisResult:
    audio_id: str
    events: list[EventoAchado]
    details: dict[str, Any] = field(default_factory=dict)


def _offset_ms(seg: Segment, posicao: int) -> int:
    """Instante aproximado de um caractere da fala (interpolação linear na duração)."""
    if not seg.text:
        return seg.offset_ms
    fracao = min(max(posicao / len(seg.text), 0.0), 1.0)
    return seg.offset_ms + round(seg.duration_ms * fracao)


def _models(config: TextConfig, client: LanguageClient) -> dict[str, str]:
    az = config.azure
    return {
        "azure-text-analytics-for-health": client.model_versions.get(OP_HEALTH, az.model_version),
        "azure-language-sentiment": client.model_versions.get(OP_SENTIMENT, az.model_version),
        "azure-language-api": API_VERSION,
        "idioma-health": az.health_language,
        "idioma-sentimento": az.sentiment_language,
    }


def analyze_transcript(
    transcript: Transcript,
    config: TextConfig,
    client: LanguageClient,
    emissor: Optional[Emissor] = None,
    *,
    matcher: Optional[TermMatcher] = None,
) -> AnalysisResult:
    matcher = matcher or TermMatcher(config.critical_terms)
    audio_id = transcript.audio_id

    # 1. Papéis pela diarização ------------------------------------------------
    paciente: list[Segment] = []
    contexto_medico: dict[int, str] = {}
    ultima_do_medico: Optional[str] = None
    contagem = defaultdict(int)
    for seg in transcript.segments:
        papel = config.speakers.role(seg.speaker)
        contagem[papel or "desconhecido"] += 1
        if papel == "medico":
            ultima_do_medico = seg.text
        elif papel == "paciente":
            if seg.text.strip():
                paciente.append(seg)
                if ultima_do_medico:
                    contexto_medico[seg.index] = ultima_do_medico
        else:
            log.warning("[%s] falante desconhecido '%s' no segmento %d; fala ignorada "
                        "(ajuste speakers no config)", audio_id, seg.speaker, seg.index)
    if not paciente:
        log.warning("[%s] nenhuma fala do paciente na transcrição", audio_id)

    # 2. Divisão por sentença ---------------------------------------------------
    documentos: list[tuple[Segment, Chunk]] = [
        (seg, chunk) for seg in paciente for chunk in chunk_text(seg.text, config.azure.max_chars_per_document)
    ]
    divididas = sum(1 for seg in paciente if len(seg.text) > config.azure.max_chars_per_document)
    if divididas:
        log.info("[%s] %d fala(s) acima de %d caracteres divididas por sentença",
                 audio_id, divididas, config.azure.max_chars_per_document)
    textos = [c.text for _, c in documentos]

    # 3. Entidades + filtro de asserção -----------------------------------------
    entidades_por_doc = client.analyze_health(textos) if textos else []
    entidades: list[dict[str, Any]] = []
    achados: list[CriticalFinding] = []
    for (seg, chunk), lista in zip(documentos, entidades_por_doc):
        for e in lista:
            posicao = chunk.offset + e.offset
            sentenca = sentence_at(seg.text, posicao)
            temporalidade, fonte = resolve_temporality(e.temporality, sentenca, config.assertions.past_markers)
            decisao = evaluate_assertion(e.certainty, e.conditionality, e.association, temporalidade)
            casamento = matcher.match(e)
            offset_ms = _offset_ms(seg, posicao)

            registro: dict[str, Any] = {
                "segmento": seg.index,
                "offset_ms": offset_ms,
                "texto": e.text,
                "categoria": e.category,
                "subcategoria": e.subcategory,
                "nome_normalizado": e.normalized_text,
                "confianca": e.confidence,
                "certeza": e.certainty,
                "condicionalidade": e.conditionality,
                "associacao": e.association,
                "temporalidade": temporalidade,
                "temporalidade_fonte": fonte,
                "sentenca": sentenca,
                "termo_critico": casamento.term.name if casamento else None,
                "casou_por": casamento.via if casamento else None,
            }

            if not decisao.keep:
                registro.update(decisao=DECISAO_DESCARTADA, motivo=decisao.reason)
                log.info("[%s] entidade descartada: '%s' (%s%s) no segmento %d, motivo %s — “%s”",
                         audio_id, e.text, e.category,
                         f", termo crítico {casamento.term.name}" if casamento else "",
                         seg.index, decisao.reason, cortar(sentenca, 120))
            elif casamento is None:
                registro.update(decisao=DECISAO_SEM_TERMO, motivo=None)
            else:
                severidade_lista = casamento.term.severity
                severidade = downgrade(severidade_lista) if decisao.downgrade else severidade_lista
                registro.update(decisao=DECISAO_TERMO, motivo=None, severidade=severidade.value,
                                rebaixada=decisao.downgrade)
                if decisao.downgrade:
                    log.info("[%s] termo crítico %s em histórico passado (%s): severidade %s → %s",
                             audio_id, casamento.term.name, fonte, severidade_lista.value, severidade.value)
                achados.append(CriticalFinding(
                    term_name=casamento.term.name,
                    severity_list=severidade_lista,
                    severity=severidade,
                    downgraded=decisao.downgrade,
                    segment=seg,
                    offset_ms=offset_ms,
                    entity_text=e.text,
                    sentence=sentenca,
                    category=e.category,
                    normalized_text=e.normalized_text,
                    confidence=e.confidence,
                    certainty=e.certainty,
                    conditionality=e.conditionality,
                    association=e.association,
                    temporality=temporalidade,
                    temporality_source=fonte,
                    matched_by=casamento.via,
                    doctor_context=contexto_medico.get(seg.index),
                ))
            entidades.append(registro)

    # 4. Sentimento por sentença ------------------------------------------------
    sentencas_por_doc = client.analyze_sentiment(textos) if textos else []
    limiar = config.sentiment.negative_threshold
    sentencas: list[dict[str, Any]] = []
    for (seg, chunk), lista in zip(documentos, sentencas_por_doc):
        for s in lista:
            sentencas.append({
                "segmento": seg.index,
                "offset_ms": _offset_ms(seg, chunk.offset + s.offset),
                "segmento_inicio_ms": seg.offset_ms,
                "segmento_fim_ms": seg.end_ms,
                "texto": s.text,
                "sentimento": s.sentiment,
                "positive": s.positive,
                "neutral": s.neutral,
                "negative": s.negative,
                "negativa": s.negative >= limiar,
            })
    total = len(sentencas)
    negativas = sum(s["negativa"] for s in sentencas)
    resumo_sentimento = {
        "total_sentencas": total,
        "sentencas_negativas": negativas,
        "pct_negativas": negativas / total if total else 0.0,
        "score_medio": {
            k: (sum(s[k] for s in sentencas) / total if total else 0.0)
            for k in ("positive", "neutral", "negative")
        },
    }
    resumo_sentimento["predominantemente_negativa"] = bool(
        total and resumo_sentimento["pct_negativas"] >= config.sentiment.min_negative_ratio
    )

    # 5. Eventos -----------------------------------------------------------------
    modelos = _models(config, client)
    fabrica = EventFactory(transcript, config, modelos)
    eventos: list[EventoAchado] = []

    por_termo: dict[str, list[CriticalFinding]] = defaultdict(list)
    for achado in achados:
        por_termo[achado.term_name].append(achado)
    for nome, ocorrencias in por_termo.items():
        principal = max(ocorrencias, key=lambda a: (a.severity.peso, a.confidence))
        eventos.append(fabrica.critical_term(principal, len(ocorrencias)))

    inicio_ms = min((s.offset_ms for s in transcript.segments), default=0)
    fim_ms = max((s.end_ms for s in transcript.segments), default=0)
    if resumo_sentimento["predominantemente_negativa"]:
        pior = max(sentencas, key=lambda s: s["negative"])
        eventos.append(fabrica.negative_sentiment(resumo_sentimento, pior, inicio_ms, fim_ms))

    if not eventos:
        eventos.append(fabrica.no_findings({
            "falas_paciente": len(paciente),
            "entidades_extraidas": len(entidades),
            "entidades_descartadas": sum(e["decisao"] == DECISAO_DESCARTADA for e in entidades),
            "total_sentencas": total,
            "pct_negativas": round(resumo_sentimento["pct_negativas"], 4),
        }, inicio_ms, fim_ms))

    eventos.sort(key=lambda ev: (-ev.severity.peso, ev.timestamp))
    if emissor is not None:
        emissor.emitir_varios(eventos)

    descartes = [e for e in entidades if e["decisao"] == DECISAO_DESCARTADA]
    log.info("[%s] %d fala(s) do paciente, %d entidade(s), %d descartada(s), %d termo(s) crítico(s), "
             "%d/%d sentença(s) negativa(s) → %d evento(s)",
             audio_id, len(paciente), len(entidades), len(descartes), len(por_termo),
             negativas, total, len(eventos))

    detalhes = {
        "audio_id": audio_id,
        "patient_id": fabrica.patient_id,
        "recorded_at": transcript.recorded_at.isoformat().replace("+00:00", "Z"),
        "transcricao": relative_uri(transcript.source),
        "model_version": config.model_version,
        "modelos": modelos,
        "lista_termos_versao": config.critical_terms.version,
        "falas": {
            "paciente": contagem["paciente"],
            "medico": contagem["medico"],
            "desconhecido": contagem["desconhecido"],
            "documentos_enviados": len(documentos),
        },
        "entidades": entidades,
        "descartes": descartes,
        "sentimento": {
            "limiar_negativo": limiar,
            "pct_minimo_negativas": config.sentiment.min_negative_ratio,
            "resumo": resumo_sentimento,
            "sentencas": sentencas,
        },
        "eventos": [str(ev.event_id) for ev in eventos],
    }
    return AnalysisResult(audio_id=audio_id, events=eventos, details=detalhes)
