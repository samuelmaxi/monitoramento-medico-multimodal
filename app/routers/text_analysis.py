"""Termos críticos e sentimento numa transcrição (US13), pelo mesmo caminho do terminal.

A análise é síncrona: uma consulta leva alguns segundos no Azure AI Language
(o health é assíncrono do lado da Azure, mas o cliente espera o resultado). Os
eventos também são publicados no barramento local (``EVENTOS_JSONL``), onde o
stub de alertas recebe os de severidade alta, como em ``audio.text.run``.
"""

from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query

from app import config as app_config
from app.schemas import TextAnalysisRequest, TextAnalysisResponse
from audio.text.alerts import assinar_alertas
from audio.text.analyze import analyze_transcript
from audio.text.config import ConfigError, TextConfig, load_config
from audio.text.language_client import LanguageAuthError, LanguageClient, LanguageServiceError
from audio.text.terms import TermMatcher
from audio.text.transcript import TranscriptError, transcript_from_dict
from contratos import BarramentoLocal, ChavePseudonimizacaoAusente

router = APIRouter(prefix="/text/analysis")


@lru_cache
def get_text_config() -> TextConfig:
    return load_config(app_config.TEXT_CONFIG_PATH)


@lru_cache
def get_matcher() -> TermMatcher:
    return TermMatcher(get_text_config().critical_terms)


@lru_cache
def get_language_client() -> LanguageClient:
    return LanguageClient.from_env(get_text_config().azure)


@lru_cache
def get_barramento() -> BarramentoLocal:
    barramento = BarramentoLocal.do_ambiente()
    assinar_alertas(barramento)
    return barramento


@router.post("", response_model=TextAnalysisResponse, response_model_exclude_none=True)
def analyze_text(
    payload: TextAnalysisRequest,
    detalhes: bool = Query(False, description="Inclui entidades, descartes e sentimento por sentença"),
) -> TextAnalysisResponse:
    try:
        transcricao = transcript_from_dict(payload.model_dump())
    except TranscriptError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        config = get_text_config()
        cliente = get_language_client()
    except (OSError, ConfigError) as exc:
        raise HTTPException(status_code=503, detail=f"Análise de texto indisponível: {exc}") from exc
    except LanguageAuthError as exc:  # variável de ambiente ausente
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        resultado = analyze_transcript(transcricao, config, cliente, get_barramento(), matcher=get_matcher())
    except ChavePseudonimizacaoAusente as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LanguageAuthError as exc:  # a Azure recusou a credencial (401/403)
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except LanguageServiceError as exc:
        status = 429 if "429" in str(exc) else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc

    return TextAnalysisResponse(
        audio_id=resultado.audio_id,
        patient_id=resultado.details["patient_id"],
        eventos=resultado.events,
        detalhes=resultado.details if detalhes else None,
    )
