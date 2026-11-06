"""US18: score de risco multimodal por paciente, com a contribuição de cada modalidade.

- ``GET  /fusao/pacientes``: último risco de cada paciente visto pelo barramento.
- ``GET  /fusao/pacientes/{patient_id}``: risco atual de um paciente.
- ``POST /fusao/risco``: calcula, sem estado, a partir de achados enviados no corpo. Com
  ``publicar=true``, os achados também entram no barramento local, como se viessem dos
  detectores: a fusão ao vivo e o motor de alertas reagem (teste ponta a ponta).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.barramento import get_barramento
from contratos import BarramentoLocal, EventoAchado
from fusao import ConfigFusao, ErroDeConfiguracao, FusaoMultimodal, Risco, evento_de_risco
from fusao.run import riscos_de

router = APIRouter(prefix="/fusao", tags=["fusão (US18)"])


class ConcordanciaResposta(BaseModel):
    eixo: str
    modalidades: list[str]
    contribuicao: float


class RiscoResposta(BaseModel):
    patient_id: str
    janela_inicio: datetime
    janela_fim: datetime
    score: float = Field(ge=0, le=1)
    nivel: str = Field(description="baixo, medio ou alto")
    severidade: str = Field(description="Severidade do risco_multimodal publicado")
    contribuicoes: dict[str, float] = Field(
        description="Parcela do score por modalidade e da concordância; a soma é o score"
    )
    forca_isolada: dict[str, float] = Field(
        description="Risco que cada modalidade indicaria sozinha"
    )
    concordancia: ConcordanciaResposta | None
    modalidades_presentes: list[str]
    modalidades_ausentes: list[str]
    score_max_isolado: float
    ganho_fusao: float
    resumo: str
    evento: EventoAchado = Field(description="risco_multimodal no contrato US04")


class RiscoRequest(BaseModel):
    eventos: list[EventoAchado] = Field(min_length=1, max_length=10_000)
    janela_min: float | None = Field(None, gt=0, description="Sobrescreve janela_min do YAML")
    ate: datetime | None = Field(
        None, description="Fim da janela (UTC). Sem ele, o achado mais recente de cada paciente"
    )


def _fusao(request: Request) -> FusaoMultimodal:
    fusao = getattr(request.app.state, "fusao", None)
    if fusao is None:
        raise HTTPException(status_code=503, detail="Fusão multimodal não iniciada")
    return fusao


def resposta(risco: Risco, config: ConfigFusao) -> RiscoResposta:
    evento = evento_de_risco(risco, config)
    conc = risco.concordancia
    return RiscoResposta(
        patient_id=risco.patient_id,
        janela_inicio=risco.inicio,
        janela_fim=risco.ate,
        score=round(risco.score, 4),
        nivel=risco.nivel,
        severidade=risco.severidade.value,
        contribuicoes={k: round(v, 4) for k, v in risco.contribuicoes.items()},
        forca_isolada={m.value: round(s.forca, 4) for m, s in risco.sinais.items()},
        concordancia=ConcordanciaResposta(
            eixo=conc.eixo,
            modalidades=[m.value for m in conc.modalidades],
            contribuicao=round(conc.contribuicao, 4),
        ) if conc else None,
        modalidades_presentes=[m.value for m in risco.presentes],
        modalidades_ausentes=[m.value for m in risco.ausentes],
        score_max_isolado=round(risco.score_max_isolado, 4),
        ganho_fusao=round(risco.ganho_fusao, 4),
        resumo=evento.evidence.summary,
        evento=evento,
    )


@router.get("/pacientes", response_model=list[RiscoResposta])
def listar(request: Request) -> list[RiscoResposta]:
    fusao = _fusao(request)
    return [resposta(r, fusao.config) for r in fusao.pacientes()]


@router.get("/pacientes/{patient_id}", response_model=RiscoResposta)
def detalhar(request: Request, patient_id: str) -> RiscoResposta:
    fusao = _fusao(request)
    risco = fusao.risco(patient_id)
    if risco is None:
        raise HTTPException(status_code=404, detail="Nenhum achado deste paciente na janela")
    return resposta(risco, fusao.config)


@router.post("/risco", response_model=list[RiscoResposta])
def calcular(
    request: Request,
    corpo: RiscoRequest,
    publicar: bool = Query(
        False,
        description="Publica os achados no barramento local: a fusão ao vivo e o motor de "
        "alertas os recebem como se viessem dos detectores",
    ),
    barramento: BarramentoLocal = Depends(get_barramento),
) -> list[RiscoResposta]:
    """Risco por paciente a partir dos achados do corpo. O cálculo não usa o estado do serviço."""
    config = _fusao(request).config
    try:
        if corpo.janela_min is not None:
            config = config.com_janela(corpo.janela_min)
    except ErroDeConfiguracao as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if corpo.ate is not None and corpo.ate.tzinfo is None:
        raise HTTPException(status_code=422, detail="ate precisa de fuso (UTC)")
    riscos = [resposta(r, config) for r in riscos_de(corpo.eventos, config, ate=corpo.ate)]
    if publicar:
        barramento.emitir_varios(sorted(corpo.eventos, key=lambda e: e.timestamp))
    return riscos

