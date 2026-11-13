"""US20: pipeline ponta a ponta. Vídeo, áudio, sinais vitais e prescrições viram achados, a fusão
calcula o risco do paciente e o motor de alertas avisa a equipe.

- ``POST /pipeline/executar``: roda o fluxo completo. Áudio e vídeo são opcionais (sem eles, as
  etapas usam o cenário de demonstração). Cada execução tem o seu diretório e o seu barramento;
  com ``publicar=true`` os achados também entram no barramento da API, e então
  ``GET /fusao/pacientes`` e ``GET /alerts`` passam a refletir a execução.
- ``GET /pipeline/execucoes/{id}``: resposta da execução (resumo, risco e relatório por etapa).
- ``GET /pipeline/execucoes/{id}/eventos``: achados publicados no barramento da execução.

A chamada é síncrona: com ``modo=real`` e vídeo, o YOLO pode levar minutos.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app import config as app_config
from app.barramento import get_barramento
from app.routers.audio_vocal import get_config_ingestao
from app.routers.fusao import RiscoResposta, resposta
from contratos import BarramentoLocal, EventoAchado, Modalidade
from pipeline import ConfigPipeline, OrquestradorPipeline
from video.nomes_video import extensao_video_suportada

log = logging.getLogger("app.pipeline")

router = APIRouter(prefix="/pipeline", tags=["pipeline ponta a ponta (US20)"])

PADRAO_ID = re.compile(r"^[0-9a-f]{12}$")
NOME_RESPOSTA = "resposta.json"


class EtapaResposta(BaseModel):
    etapa: str
    status: str = Field(description="sucesso ou falha")
    duracao_ms: float
    eventos_emitidos: int
    detalhes: dict[str, Any] = Field(
        description="O que a etapa usou (serviço Azure real ou simulado, fonte dos dados, erro)"
    )


class ExecucaoResposta(BaseModel):
    execucao_id: str
    status: Literal["sucesso", "parcial"] = Field(
        description="parcial quando alguma etapa falhou (o fluxo segue com as demais)"
    )
    modo: str
    patient_id: str
    bed_id: str
    duracao_total_ms: float
    total_eventos: int = Field(description="Achados publicados no barramento da execução")
    total_alertas: int
    etapas_com_falha: list[str]
    risco: RiscoResposta | None = Field(
        description="Risco multimodal do paciente; vazio se nenhum achado chegou à fusão"
    )
    etapas: list[EtapaResposta]
    publicado_no_barramento: bool


def _pasta(execucao_id: str) -> Path:
    if not PADRAO_ID.fullmatch(execucao_id):
        raise HTTPException(status_code=404, detail="Execução não encontrada")
    pasta = Path(app_config.PIPELINE_API_SAIDA_DIR) / execucao_id
    if not pasta.is_dir():
        raise HTTPException(status_code=404, detail="Execução não encontrada")
    return pasta


def _salvar_upload(arquivo: UploadFile, destino: Path, limite_mb: int, rotulo: str) -> Path:
    limite = limite_mb * 1024 * 1024
    destino.parent.mkdir(parents=True, exist_ok=True)
    gravados = 0
    with destino.open("wb") as saida:
        for bloco in iter(lambda: arquivo.file.read(1024 * 1024), b""):
            gravados += len(bloco)
            if gravados > limite:
                raise HTTPException(
                    status_code=413, detail=f"O {rotulo} excede o limite de {limite_mb} MB."
                )
            saida.write(bloco)
    return destino


def _enviado(arquivo: UploadFile | None) -> UploadFile | None:
    """Clientes (Swagger, Postman) mandam a parte vazia quando nenhum arquivo é escolhido."""
    return arquivo if arquivo is not None and arquivo.filename else None


@router.post("/executar", response_model=ExecucaoResposta)
def executar(
    audio_file: UploadFile | None = File(None, description="Áudio da consulta (Speech + voz)"),
    video_file: UploadFile | None = File(None, description="Vídeo do leito (YOLO, só no modo real)"),
    modo: Literal["simulado", "real"] = Form(
        "simulado", description="real usa as chaves Azure e o YOLO do servidor"
    ),
    patient_id: str | None = Form(None, description="ID de origem ou pseudônimo (pt_...)"),
    bed_id: str = Form("UTI-07"),
    medico_falante: int = Form(1, ge=1, description="Falante que é o médico, na diarização"),
    velocidade: float = Form(
        0.0, ge=0, description="Aceleração do replay dos sinais vitais; 0 = sem espera"
    ),
    sinais_fonte: Literal["auto", "mimic", "sintetico"] = Form("auto"),
    abortar_na_falha: bool = Form(False),
    publicar: bool = Form(
        False,
        description="Publica os achados no barramento da API (fusão ao vivo e alertas)",
    ),
    barramento: BarramentoLocal = Depends(get_barramento),
) -> ExecucaoResposta:
    audio_file, video_file = _enviado(audio_file), _enviado(video_file)
    execucao_id = uuid.uuid4().hex[:12]
    pasta = Path(app_config.PIPELINE_API_SAIDA_DIR) / execucao_id

    try:
        audio_path = video_path = None
        if audio_file is not None:
            extensao = (audio_file.filename or "").rsplit(".", 1)[-1].lower()
            aceitas = get_config_ingestao().validacao.extensoes
            if extensao not in aceitas:
                raise HTTPException(
                    status_code=400,
                    detail=f"Formato de áudio não suportado: '{extensao}'. Aceitos: {sorted(aceitas)}.",
                )
            audio_path = _salvar_upload(
                audio_file, pasta / "entrada" / f"consulta.{extensao}",
                app_config.AUDIO_MAX_UPLOAD_MB, "áudio",
            )
        if video_file is not None:
            extensao = Path(video_file.filename or "").suffix.lower()
            if not extensao_video_suportada(extensao):
                raise HTTPException(
                    status_code=400, detail=f"Formato de vídeo não suportado: '{extensao}'."
                )
            video_path = _salvar_upload(
                video_file, pasta / "entrada" / f"leito{extensao}",
                app_config.VIDEO_MAX_UPLOAD_MB, "vídeo",
            )

        config = ConfigPipeline.from_env(
            modo=modo,
            paciente_id=patient_id or "demo-uti07",
            leito_id=bed_id,
            fator_velocidade=velocidade,
            saida_dir=pasta,
            sinais_fonte=sinais_fonte,
            audio_path=audio_path,
            video_path=video_path,
            medico_falante=medico_falante,
            abortar_na_falha=abortar_na_falha,
        )
        resumo = OrquestradorPipeline(config).executar()
    except HTTPException:
        shutil.rmtree(pasta, ignore_errors=True)
        raise
    except Exception as exc:
        log.exception("pipeline %s falhou fora das etapas", execucao_id)
        raise HTTPException(status_code=500, detail=f"Falha ao executar o pipeline: {exc}") from exc

    relatorio = json.loads(resumo.caminho_relatorio.read_text(encoding="utf-8"))
    if publicar:
        achados = _ler_eventos(resumo.caminho_eventos)
        # A fusão ao vivo refaz o risco; o risco_multimodal da execução não é republicado.
        achados = [e for e in achados if e.modality is not Modalidade.FUSAO]
        barramento.emitir_varios(sorted(achados, key=lambda e: e.timestamp))

    corpo = ExecucaoResposta(
        execucao_id=execucao_id,
        status="parcial" if resumo.etapas_com_falha else "sucesso",
        modo=config.modo,
        patient_id=resumo.paciente_id,
        bed_id=resumo.leito_id,
        duracao_total_ms=resumo.duracao_total_ms,
        total_eventos=resumo.total_eventos_barramento,
        total_alertas=resumo.total_alertas,
        etapas_com_falha=list(resumo.etapas_com_falha),
        risco=resposta(resumo.risco, resumo.config_fusao) if resumo.risco else None,
        etapas=[EtapaResposta(**e) for e in relatorio["etapas"]],
        publicado_no_barramento=publicar,
    )
    (pasta / NOME_RESPOSTA).write_text(corpo.model_dump_json(indent=2), encoding="utf-8")
    return corpo


@router.get("/execucoes/{execucao_id}", response_model=ExecucaoResposta)
def detalhar(execucao_id: str) -> ExecucaoResposta:
    arquivo = _pasta(execucao_id) / NOME_RESPOSTA
    if not arquivo.is_file():
        raise HTTPException(status_code=404, detail="Execução sem resposta gravada")
    return ExecucaoResposta.model_validate_json(arquivo.read_text(encoding="utf-8"))


@router.get("/execucoes/{execucao_id}/eventos", response_model=list[EventoAchado])
def eventos(execucao_id: str) -> list[EventoAchado]:
    return _ler_eventos(_pasta(execucao_id) / "eventos.jsonl")


def _ler_eventos(caminho: Path) -> list[EventoAchado]:
    if not caminho.is_file():
        return []
    linhas = caminho.read_text(encoding="utf-8").splitlines()
    return [EventoAchado.model_validate_json(linha) for linha in linhas if linha.strip()]
