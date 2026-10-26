"""Áudio da consulta → transcrição (US11) → termos críticos e sentimento (US13).

Junta ``POST /transcription`` e ``POST /text/analysis`` numa chamada. Tudo que
não depende da Azure (formato, metadados, config e credenciais da análise,
chave de pseudonimização) é checado antes de gastar a chamada ao Speech. O
áudio e o JSON da transcrição ficam em ``<TRANSCRIPTION_OUTPUT_DIR>/<job_id>/``,
como na rota de transcrição.
"""

from fastapi import APIRouter, Form, HTTPException, Query, UploadFile

from app.routers.text_analysis import analisar, ler_transcricao, verificar_disponibilidade
from app.routers.transcription import (
    erro_transcricao,
    salvar_audio,
    transcrever,
    validar_formato,
    validar_metadados_form,
)
from app.schemas import AudioAnalysisResponse
from app.transcription_client import TranscricaoError
from audio.text.events import patient_pseudonym, relative_uri
from contratos import ChavePseudonimizacaoAusente

router = APIRouter(prefix="/audio/analysis")


@router.post("", response_model=AudioAnalysisResponse, response_model_exclude_none=True)
def analyze_audio(
    audio_file: UploadFile,
    patient_id: str = Form(description="ID de origem ou pseudônimo pt_…; pseudonimizado antes de sair"),
    recorded_at: str = Form(description="Início da gravação, ISO-8601 UTC (ex.: 2026-10-01T13:00:00Z)"),
    medico_falante: int = Form(
        description="Número do falante (diarização) que é o médico; os demais viram paciente. "
        "Só a fala do paciente vai para a análise"
    ),
    audio_id: str | None = Form(None, description="ID curto da consulta, sem dados pessoais. Padrão: o job_id"),
    max_falantes: int = Form(2, ge=2, description="Máximo de falantes na diarização"),
    detalhes: bool = Query(False, description="Inclui entidades, descartes e sentimento por sentença"),
) -> AudioAnalysisResponse:
    extension = validar_formato(audio_file)
    recorded_at = validar_metadados_form(audio_id, recorded_at)
    verificar_disponibilidade()
    try:
        patient_pseudonym(patient_id)
    except ChavePseudonimizacaoAusente as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    job_id, caminho_audio = salvar_audio(audio_file, extension)
    try:
        documento = transcrever(
            caminho_audio,
            audio_id=audio_id or job_id,
            patient_id=patient_id,
            recorded_at=recorded_at,
            medico_falante=medico_falante,
            max_speakers=max_falantes,
        )
    except TranscricaoError as exc:
        raise erro_transcricao(exc, caminho_audio) from exc

    caminho_json = caminho_audio.with_suffix(".json")
    analise = analisar(ler_transcricao(documento, source=caminho_json), detalhes)
    return AudioAnalysisResponse(
        audio_id=analise.audio_id,
        patient_id=analise.patient_id,
        transcricao_uri=relative_uri(caminho_json),
        transcricao=documento,
        eventos=analise.eventos,
        detalhes=analise.detalhes,
    )
