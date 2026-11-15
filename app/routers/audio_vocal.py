"""Biomarcadores vocais e achados de áudio (US10, US12, US14): ``POST /audio/vocal``.

Recebe a gravação, converte para WAV PCM 16 kHz mono (US10), mede os biomarcadores do paciente
(US12) e classifica (US14). Os eventos vão para o barramento compartilhado
(``app.barramento.get_barramento()``): os de severidade alta chegam ao motor de alertas (US19).

Tudo que não depende do áudio (formato, metadados, configuração, credenciais, chave de
pseudonimização) é checado antes de gravar o upload e antes de gastar a chamada ao Speech. O
áudio enviado é preservado em ``<AUDIO_SAIDA_DIR>/<audio_id>/original.<ext>``.

O resultado é **indicativo para avaliação clínica, não diagnóstico**.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException, Query, UploadFile

from app.barramento import get_barramento
from app.config import (
    AUDIO_INGESTAO_CONFIG_PATH,
    AUDIO_MAX_UPLOAD_MB,
    AUDIO_SAIDA_DIR,
    AUDIO_VOCAL_CONFIG_PATH,
    AUDIO_YAMNET_MODEL_PATH,
    require_azure_speech_endpoint,
    require_azure_speech_key,
)
from app.routers.transcription import erro_transcricao, transcrever, validar_metadados_form
from app.schemas import AudioVocalResponse
from app.transcription_client import TranscricaoError
from audio.ingestao.config import ConfigIngestao
from audio.ingestao.config import carregar_config as carregar_config_ingestao
from audio.ingestao.erros import AudioIdInvalido, ErroDeAudio, FormatoNaoSuportado
from audio.ingestao.lote import validar_audio_id
from audio.text.events import patient_pseudonym
from audio.vocal.baseline import RepositorioBaseline
from audio.vocal.classificacao import AVISO
from audio.vocal.config import ConfigVocal, ErroDeConfiguracao, carregar_config
from audio.vocal.erros import ErroDeAnalise
from audio.vocal.respiratorio import DetectorRespiratorio, ErroDeModelo, carregar_detector
from audio.vocal.run import processar
from contratos import ChavePseudonimizacaoAusente

log = logging.getLogger("audio.vocal")

router = APIRouter(prefix="/audio/vocal", tags=["US10/US12/US14 · áudio"])

_detector: DetectorRespiratorio | None = None


@lru_cache
def get_config_vocal() -> ConfigVocal:
    return carregar_config(AUDIO_VOCAL_CONFIG_PATH)


@lru_cache
def get_config_ingestao() -> ConfigIngestao:
    return carregar_config_ingestao(AUDIO_INGESTAO_CONFIG_PATH)


def get_detector() -> DetectorRespiratorio | None:
    """YAMNet, carregado na primeira chamada. Sem o modelo baixado devolve ``None`` (e avisa)."""
    global _detector
    if _detector is None:
        _detector = carregar_detector(get_config_vocal().respiratorio, AUDIO_YAMNET_MODEL_PATH)
    return _detector


def verificar_disponibilidade(transcrever_com_azure: bool) -> None:
    """503 se a configuração do módulo (ou, se pedida, a do Azure Speech) estiver faltando."""
    try:
        get_config_vocal()
        get_config_ingestao()
        if transcrever_com_azure:
            require_azure_speech_key()
            require_azure_speech_endpoint()
    except (OSError, ErroDeConfiguracao, ErroDeModelo, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"Análise vocal indisponível: {exc}") from exc


def _exigir_formato(audio_file: UploadFile, cfg: ConfigIngestao) -> str:
    extensao = (audio_file.filename or "").rsplit(".", 1)[-1].lower()
    if extensao not in cfg.validacao.extensoes:
        raise HTTPException(
            status_code=400,
            detail=(f"Formato de áudio não suportado: '{extensao}'. "
                    f"Aceitos: {sorted(cfg.validacao.extensoes)}."),
        )
    return extensao


def _salvar_original(audio_file: UploadFile, pasta: Path, extensao: str) -> Path:
    """Grava o upload antes de qualquer processamento, com limite de tamanho."""
    limite = AUDIO_MAX_UPLOAD_MB * 1024 * 1024
    destino = pasta / f"original.{extensao}"
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        gravados = 0
        with destino.open("wb") as saida:
            for bloco in iter(lambda: audio_file.file.read(1024 * 1024), b""):
                gravados += len(bloco)
                if gravados > limite:
                    raise HTTPException(
                        status_code=413,
                        detail=f"O áudio excede o limite de {AUDIO_MAX_UPLOAD_MB} MB.")
                saida.write(bloco)
    except HTTPException:
        shutil.rmtree(pasta, ignore_errors=True)
        raise
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Não foi possível salvar o áudio: {exc}") from exc
    return destino


def _transcritor_da_api(convertido: Path, *, audio_id: str, patient_id: str, recorded_at: str,
                        medico_falante: int | None):
    """Transcrição do Azure Speech (US11) com os mesmos códigos de erro de ``/transcription``."""
    try:
        return transcrever(convertido, audio_id=audio_id, patient_id=patient_id,
                           recorded_at=recorded_at, medico_falante=medico_falante)
    except TranscricaoError as exc:
        raise erro_transcricao(exc, convertido) from exc


@router.post("", response_model=AudioVocalResponse, response_model_exclude_none=True)
def analisar_voz(
    audio_file: UploadFile,
    patient_id: str = Form(
        description="ID de origem ou pseudônimo pt_…; pseudonimizado antes de sair"),
    recorded_at: str = Form(
        description="Início da gravação, ISO-8601 UTC (ex.: 2026-10-01T13:00:00Z)"),
    audio_id: str | None = Form(
        None, description="ID curto da gravação, sem dados pessoais. Padrão: gerado"),
    medico_falante: int | None = Form(
        None, description="Número do falante (diarização) que é o médico. Sem ele, só vale áudio "
        "de um único falante (tratado como paciente)"),
    transcrever_audio: bool = Form(
        False, alias="transcrever",
        description="Transcreve com o Azure Speech (US11): dá a taxa de fala e separa médico "
        "e paciente"),
    bed_id: str | None = Form(None, description="Leito, para o contexto do alerta"),
    encounter_id: str | None = Form(None, description="Internação (ID de origem ou enc_…)"),
    origem: str | None = Form(
        None, description="Origem da gravação, registrada nos metadados (US10)"),
    licenca: str | None = Form(
        None, description="Licença/base legal de uso, registrada nos metadados (US10)"),
    detalhes: bool = Query(
        False, description="Inclui o relatório completo (segmentos, critérios, baseline)"),
    publicar: bool = Query(True, description="Publica os eventos no barramento (e nos alertas)"),
    atualizar_baseline: bool = Query(True, description="Registra o áudio na baseline do paciente"),
) -> AudioVocalResponse:
    verificar_disponibilidade(transcrever_audio)
    cfg_ingestao, cfg = get_config_ingestao(), get_config_vocal()
    extensao = _exigir_formato(audio_file, cfg_ingestao)
    gravado_em = validar_metadados_form(audio_id, recorded_at)
    try:
        patient_pseudonym(patient_id)
    except ChavePseudonimizacaoAusente as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    id_do_audio = audio_id or f"audio-{uuid4().hex[:12]}"
    try:
        validar_audio_id(id_do_audio)
    except AudioIdInvalido as exc:
        raise HTTPException(status_code=422, detail=f"{exc.codigo}: {exc}") from exc
    saida = Path(AUDIO_SAIDA_DIR)
    if (saida / id_do_audio).exists():  # não sobrescreve um original já preservado
        raise HTTPException(
            status_code=409, detail=f"O audio_id '{id_do_audio}' já foi usado; envie com outro.")
    original = _salvar_original(audio_file, saida / id_do_audio, extensao)
    instante = datetime.fromisoformat((gravado_em or recorded_at).replace("Z", "+00:00"))

    try:
        execucao = processar(
            original, patient_id=patient_id, recorded_at=instante, saida=saida,
            cfg_ingestao=cfg_ingestao, cfg=cfg, audio_id=id_do_audio,
            medico_falante=medico_falante, transcrever=transcrever_audio,
            transcritor=_transcritor_da_api, detector=get_detector(),
            repositorio=RepositorioBaseline(saida / "baseline"),
            atualizar_baseline=atualizar_baseline,
            emissor=get_barramento() if publicar else None,
            bed_id=bed_id, encounter_id=encounter_id, origem_audio=origem, licenca=licenca)
    except FormatoNaoSuportado as exc:
        raise HTTPException(status_code=400, detail=f"{exc.codigo}: {exc}") from exc
    except (ErroDeAudio, ErroDeAnalise) as exc:
        codigo = getattr(exc, "codigo", "analise_invalida")
        raise HTTPException(status_code=422, detail=f"{codigo}: {exc}") from exc
    except ChavePseudonimizacaoAusente as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    resultado = execucao.resultado
    return AudioVocalResponse(
        audio_id=resultado.audio_id,
        patient_id=resultado.patient_id,
        classe=resultado.classe,
        classes=resultado.relatorio["classes"],
        aviso=AVISO,
        biomarcadores=resultado.biomarcadores.para_dict(),
        eventos=resultado.eventos,
        publicados=len(resultado.eventos) if publicar else 0,
        metadados=execucao.ingestao.metadados.para_dict(),
        detalhes=resultado.relatorio if detalhes else None,
    )
