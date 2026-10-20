from typing import Literal

from pydantic import BaseModel

from contratos import Artefato, Contexto, Janela
from contratos.evento import UtcDatetime


class PalavraTranscrita(BaseModel):
    texto: str
    offset_ms: int | None = None
    duracao_ms: int | None = None


class FalaTranscrita(BaseModel):
    texto: str
    falante: int | None = None
    papel: Literal["medico", "paciente"] | None = None
    offset_ms: int | None = None
    duracao_ms: int | None = None
    confianca: float | None = None
    palavras: list[PalavraTranscrita] = []


class TranscricaoResponse(BaseModel):
    """Mesmo documento gravado em ``saida/transcricao/<job>/audio.json``."""

    schema_version: str
    arquivo: dict[str, str | int]
    idioma: str
    duracao_ms: int | None = None
    diarizacao: dict[str, bool | int]
    frases_medicas: bool
    medico_falante: int | None = None
    criado_em: str
    texto_completo: str
    falas: list[FalaTranscrita]


class SentimentRequest(BaseModel):
    text: str
    language_code: str = "pt"


class SentimentResponse(BaseModel):
    sentiment: str
    sentiment_score: dict[str, float]


class PoseJobStartResponse(BaseModel):
    job_id: str
    status: str


class PoseSummary(BaseModel):
    """Mesmas colunas do ``summary.csv`` gerado por ``video.pose.run``."""

    frames_processados: int
    frames_com_esqueleto: int
    pct_frames_com_esqueleto: float
    fps_origem: float
    fps_processado: float
    duracao_s: float
    tempo_processamento_s: float
    modelo: str
    backend: str


class PoseAngleStats(BaseModel):
    """Resumo de um ângulo (graus) nos quadros em que ele pôde ser calculado."""

    quadros_validos: int
    min: float | None = None
    max: float | None = None
    media: float | None = None


class PoseJobResponse(BaseModel):
    """Resultado de um job de pose.

    ``patient_id``, ``context``, ``ingested_at``, ``window``, ``model_version`` e
    ``artifacts`` usam os tipos e nomes do contrato ``EventoAchado``, para o
    detector de desvios (US08) montar o evento direto a partir daqui.
    """

    job_id: str
    status: Literal["QUEUED", "IN_PROGRESS", "COMPLETED", "FAILED"]
    failure_reason: str | None = None
    patient_id: str | None = None
    context: Contexto | None = None
    ingested_at: UtcDatetime
    window: Janela | None = None
    model_version: str
    summary: PoseSummary | None = None
    angles: dict[str, PoseAngleStats] | None = None
    artifacts: list[Artefato] = []


class PoseFeedbackResponse(BaseModel):
    """Resultado do vídeo em linguagem natural (``video.pose.feedback``)."""

    job_id: str
    resumo: str
    pontos_positivos: list[str]
    pontos_negativos: list[str]
    pontos_de_alerta: list[str]
    aviso: str
