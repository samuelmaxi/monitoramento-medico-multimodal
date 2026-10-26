from typing import Any, Literal

from pydantic import BaseModel, Field

from contratos import Artefato, Contexto, EventoAchado, Janela
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
    audio_id: str | None = Field(
        default=None, examples=["consulta-07"], description="Vira context.source_id na US13; sem dados pessoais"
    )
    patient_id: str | None = Field(
        default=None, description="ID de origem ou pseudônimo pt_…; a US13 pseudonimiza antes de emitir eventos"
    )
    recorded_at: str | None = Field(
        default=None, examples=["2026-10-01T13:00:00Z"], description="Início da gravação, ISO-8601 UTC"
    )
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


class TranscriptSegment(BaseModel):
    speaker: str = Field(examples=["paciente"], description="Rótulo da diarização; só o paciente vai para a Azure")
    text: str
    offset_ms: int = Field(ge=0, description="Início da fala, relativo ao início da gravação")
    duration_ms: int = Field(ge=0)
    confidence: float | None = None


class TextAnalysisRequest(BaseModel):
    """Transcrição diarizada no formato da US11 (``audio/text/transcript.py``)."""

    audio_id: str = Field(examples=["consulta-07"], description="Vira context.source_id; sem dados pessoais")
    patient_id: str = Field(description="ID de origem ou pseudônimo pt_…; pseudonimizado antes de sair")
    recorded_at: str = Field(examples=["2026-10-01T13:00:00Z"], description="Início da gravação, ISO-8601 UTC")
    is_synthetic: bool = False
    segments: list[TranscriptSegment]


class TextAnalysisResponse(BaseModel):
    """Eventos da US13 (contrato ``EventoAchado``, ``modality="texto"``)."""

    audio_id: str
    patient_id: str = Field(description="Pseudônimo usado nos eventos")
    eventos: list[EventoAchado]
    detalhes: dict[str, Any] | None = Field(
        default=None, description="Entidades, descartes e sentimento por sentença (com ?detalhes=true)"
    )


class AudioAnalysisResponse(BaseModel):
    """Transcrição (US11) seguida dos termos críticos e do sentimento na fala do paciente (US13)."""

    audio_id: str
    patient_id: str = Field(description="Pseudônimo usado nos eventos")
    transcricao_uri: str | None = Field(
        default=None, description="JSON da transcrição gravado no servidor (relativo à raiz do repositório)"
    )
    transcricao: TranscricaoResponse
    eventos: list[EventoAchado]
    detalhes: dict[str, Any] | None = Field(
        default=None, description="Entidades, descartes e sentimento por sentença (com ?detalhes=true)"
    )
