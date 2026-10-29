"""US15: NEWS2 sob demanda e detecção de anomalias em streaming de sinais vitais."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.routers.video_pose import _pseudonym
from contratos import (
    ChavePseudonimizacaoAusente,
    EventoAchado,
    Janela,
    ValidationError,
    pseudonimizar_paciente,
)
from contratos.pseudonimizacao import PADRAO_PACIENTE
from sinais import DetectorSinais, Medicao, news2
from sinais.limpeza import plausivel

router = APIRouter(prefix="/sinais-vitais")

Sinal = Literal["fc", "pas", "pad", "spo2", "fr", "temp", "o2_suplementar", "alerta"]

# Estado em memória do processo: a baseline de cada paciente vem das medições já enviadas.
# shortcut: some ao reiniciar a API e não é compartilhado entre workers;
# trocar por Redis/Event Hubs se escalar.
_detector = DetectorSinais()


class News2Request(BaseModel):
    fc: float | None = Field(None, description="Frequência cardíaca (bpm)")
    pas: float | None = Field(None, description="PA sistólica (mmHg)")
    spo2: float | None = Field(None, description="SpO2 escala 1 (%)")
    fr: float | None = Field(None, description="Frequência respiratória (irpm)")
    temp: float | None = Field(None, description="Temperatura (°C)")
    o2_suplementar: bool | None = None
    alerta: bool | None = Field(None, description="True = Alerta; False = C, V, P ou U")


class News2Response(BaseModel):
    pontos: dict[str, int]
    total: int
    gatilho: str | None
    ausentes: list[str]
    artefatos: list[str] = Field(
        description="Parâmetros descartados por valor fisiologicamente impossível"
    )


class MedicaoRequest(BaseModel):
    patient_id: str = Field(
        description="Pseudônimo pt_<hex> ou ID de origem (pseudonimizado na entrada)"
    )
    timestamp: datetime
    sinal: Sinal
    valor: float


class MedicoesRequest(BaseModel):
    medicoes: list[MedicaoRequest] = Field(min_length=1, max_length=10_000)


class MedicoesResponse(BaseModel):
    eventos: list[EventoAchado]
    artefatos_descartados: int


@router.post("/news2", response_model=News2Response)
def score_news2(body: News2Request) -> News2Response:
    valores = body.model_dump(exclude_none=True)
    artefatos = [s for s, v in valores.items() if not plausivel(s, v)]
    resultado = news2.calcular({s: v for s, v in valores.items() if s not in artefatos})
    return News2Response(
        pontos=resultado.pontos,
        total=resultado.total,
        gatilho=resultado.gatilho,
        ausentes=list(resultado.ausentes),
        artefatos=artefatos,
    )


@router.post("/medicoes", response_model=MedicoesResponse, response_model_exclude_none=True)
def ingest_measurements(body: MedicoesRequest) -> MedicoesResponse:
    """Processa as medições em ordem de tempo e devolve os achados (NEWS2 e ML) que elas geraram."""
    try:
        medicoes = [
            Medicao(
                _pseudonym(m.patient_id, PADRAO_PACIENTE, pseudonimizar_paciente),
                Janela(start=m.timestamp, end=m.timestamp).start,  # o contrato só aceita UTC
                m.sinal,
                m.valor,
            )
            for m in body.medicoes
        ]
    except ChavePseudonimizacaoAusente as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_url=False, include_context=False, include_input=False),
        ) from exc

    artefatos_antes = _detector.artefatos.total()
    eventos = [
        e for m in sorted(medicoes, key=lambda m: m.instante) for e in _detector.processar(m)
    ]
    return MedicoesResponse(
        eventos=eventos, artefatos_descartados=_detector.artefatos.total() - artefatos_antes
    )
