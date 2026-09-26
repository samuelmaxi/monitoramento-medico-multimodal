"""Contrato único de achado/evento (US04).

Todo módulo (vídeo, áudio, texto, sinais vitais, prescrições, movimentação e
fusão) emite instâncias de :class:`EventoAchado`. Nenhum módulo publica dict
solto: use :meth:`EventoAchado.criar` e um emissor de ``contratos.emissor``.

Campos exigidos pela DoD: ``patient_id`` pseudonimizado, ``modality``,
``timestamp`` ISO-8601 UTC, ``event_type``, ``score`` (0–1), ``severity``,
``evidence`` e ``model_version``. Os demais dão suporte à rastreabilidade
(``event_id``, ``detected_at``, ``ingested_at``), à fusão (``window``,
``related_event_ids``) e aos alertas (``context``).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PlainSerializer,
    StrictBool,
    StrictFloat,
    StrictInt,
    ValidationError,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from .catalogo import SEM_ACHADOS, TIPOS_DE_EVENTO, Modalidade, Severidade, tipos_validos
from .pseudonimizacao import PADRAO_INTERNACAO, PADRAO_PACIENTE

SCHEMA_VERSION = "1.0.0"

TAMANHO_MAX_BYTES = 256 * 1024
"""Evento leve: evidências pesadas (frames, trechos de áudio, relatórios) ficam
em arquivo e entram aqui como URI. 256 KB também é o limite de mensagem de
filas e barramentos gerenciados, caso o projeto migre para a nuvem."""

PADRAO_UTC = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|\+00:00)$"
PADRAO_MODEL_VERSION = r"^[a-z0-9][a-z0-9._-]*@[0-9A-Za-z][0-9A-Za-z._+-]*$"
PADRAO_URI_ARTEFATO = r"^((s3|file|https)://\S+|[A-Za-z0-9_-][A-Za-z0-9_./-]*)$"
PADRAO_ID_CURTO = r"^[A-Za-z0-9_.-]{1,64}$"

# Guarda-corpo contra PII em texto livre. Não substitui revisão humana.
_RE_CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


# --------------------------------------------------------------------------- #
# Tipos auxiliares
# --------------------------------------------------------------------------- #

def _exigir_utc(valor: datetime) -> datetime:
    if valor.tzinfo is None or valor.utcoffset() is None:
        raise ValueError("datetime sem fuso horário: use UTC, ex. datetime.now(timezone.utc)")
    if valor.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp precisa estar em UTC (sufixo Z ou +00:00)")
    return valor.astimezone(timezone.utc)


def _formatar_utc(valor: datetime) -> str:
    return valor.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


UtcDatetime = Annotated[
    datetime,
    AfterValidator(_exigir_utc),
    PlainSerializer(_formatar_utc, return_type=str),
    WithJsonSchema({"type": "string", "format": "date-time", "pattern": PADRAO_UTC}),
]


def _sem_pii(texto: str) -> str:
    if _RE_CPF.search(texto) or _RE_EMAIL.search(texto):
        raise ValueError("texto contém padrão de CPF ou e-mail; remova dados pessoais")
    return texto


TextoSemPII = Annotated[str, AfterValidator(_sem_pii)]
# Tipos estritos: sem eles, "12345678909" viraria int e escaparia da checagem de PII.
ValorLimiar = Union[StrictBool, StrictInt, StrictFloat, Annotated[str, Field(max_length=200)]]
ValorFeature = Union[StrictBool, StrictInt, StrictFloat, Annotated[TextoSemPII, Field(max_length=200)], None]


def agora_utc() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Submodelos
# --------------------------------------------------------------------------- #

class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Artefato(_Base):
    """Referência a uma evidência armazenada fora do evento.

    ``uri`` é um caminho relativo à raiz do repositório (ex. ``saida/video/frame.jpg``)
    ou uma URI ``s3://``/``https://``. Caminhos absolutos são recusados porque só
    funcionam na máquina de quem gerou o evento.
    """

    kind: Literal[
        "frame", "clip", "audio_segment", "transcricao", "relatorio",
        "serie_temporal", "prescricao", "outro",
    ]
    uri: str = Field(pattern=PADRAO_URI_ARTEFATO, description="Caminho relativo à raiz do repositório, ou URI s3:// / https://")
    media_type: str | None = Field(default=None, examples=["image/jpeg", "audio/wav"])
    start_ms: NonNegativeInt | None = Field(default=None, description="Início relativo à mídia (ms)")
    end_ms: NonNegativeInt | None = Field(default=None, description="Fim relativo à mídia (ms)")

    @model_validator(mode="after")
    def _intervalo(self) -> "Artefato":
        if self.start_ms is not None and self.end_ms is not None and self.end_ms < self.start_ms:
            raise ValueError("end_ms não pode ser menor que start_ms")
        return self


class Evidencia(_Base):
    """Por que o achado foi gerado. Serve à explicabilidade exigida em US14, US16, US18 e US19."""

    summary: TextoSemPII = Field(
        min_length=3, max_length=500,
        description="Frase legível em pt-BR para a equipe médica",
    )
    features: dict[str, ValorFeature] = Field(
        default_factory=dict, max_length=50,
        description="Valores observados que levaram à decisão (ex.: spo2, news2_total, jitter_local_pct)",
    )
    thresholds: dict[str, ValorLimiar] = Field(
        default_factory=dict, max_length=50,
        description="Limiares aplicados (ex.: news2_emergencia: 7)",
    )
    models: dict[str, str] = Field(
        default_factory=dict, max_length=20,
        description="Modelos/serviços de base e versões (ex.: yolov8n: 8.3.40, amazon-transcribe: pt-BR)",
    )
    artifacts: list[Artefato] = Field(default_factory=list, max_length=20)


class Janela(_Base):
    """Intervalo de tempo real (UTC) coberto pelo achado."""

    start: UtcDatetime
    end: UtcDatetime

    @model_validator(mode="after")
    def _ordem(self) -> "Janela":
        if self.end < self.start:
            raise ValueError("window.end não pode ser anterior a window.start")
        return self


class Contexto(_Base):
    """Metadados não identificáveis usados pelo motor de alertas (US19)."""

    encounter_id: str | None = Field(default=None, pattern=PADRAO_INTERNACAO)
    bed_id: str | None = Field(default=None, pattern=PADRAO_ID_CURTO)
    source_id: str | None = Field(
        default=None, pattern=PADRAO_ID_CURTO,
        description="ID da mídia/câmera/sensor de origem, sem dado pessoal",
    )
    procedure_type: str | None = Field(default=None, pattern=PADRAO_ID_CURTO, examples=["fisioterapia"])


# --------------------------------------------------------------------------- #
# Evento
# --------------------------------------------------------------------------- #

class EventoAchado(_Base):
    """Achado clínico emitido por qualquer módulo do pipeline multimodal."""

    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    event_id: uuid.UUID = Field(description="UUID v4 único do evento (chave de idempotência)")
    patient_id: str = Field(pattern=PADRAO_PACIENTE, description="Pseudônimo HMAC-SHA256: pt_<24 hex>")
    modality: Modalidade
    event_type: str = Field(description="Tipo de achado; precisa pertencer ao catálogo da modalidade")
    timestamp: UtcDatetime = Field(description="Quando o fenômeno ocorreu (tempo do evento, UTC)")
    detected_at: UtcDatetime = Field(description="Quando o módulo emitiu o achado (UTC)")
    ingested_at: UtcDatetime | None = Field(
        default=None, description="Quando o dado bruto entrou no sistema; base da latência medida na US19",
    )
    window: Janela | None = None
    score: float = Field(ge=0.0, le=1.0, description="Intensidade/confiança normalizada da anomalia")
    severity: Severidade
    evidence: Evidencia
    model_version: str = Field(
        pattern=PADRAO_MODEL_VERSION,
        description="<componente>@<versão> do módulo emissor, ex. anomalias-sinais@0.2.0",
    )
    context: Contexto | None = None
    related_event_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=200,
        description="Eventos que originaram este (obrigatório para fusão)",
    )
    is_synthetic: bool = Field(default=False, description="True para anomalias injetadas em teste (US03)")

    @field_validator("event_type")
    @classmethod
    def _event_type_conhecido(cls, valor: str) -> str:
        todos = {t for tipos in TIPOS_DE_EVENTO.values() for t in tipos}
        if valor not in todos:
            raise ValueError(f"event_type '{valor}' não existe no catálogo (contratos/catalogo.py)")
        return valor

    @model_validator(mode="after")
    def _regras_de_negocio(self) -> "EventoAchado":
        if self.event_type not in tipos_validos(self.modality):
            permitidos = ", ".join(sorted(tipos_validos(self.modality)))
            raise ValueError(
                f"event_type '{self.event_type}' não pertence à modalidade '{self.modality.value}'. "
                f"Permitidos: {permitidos}"
            )
        if self.event_type == SEM_ACHADOS and self.severity is not Severidade.INFO:
            raise ValueError("event_type 'sem_achados' exige severity 'info'")
        if self.modality is Modalidade.FUSAO and not self.related_event_ids:
            raise ValueError("eventos de fusão precisam listar related_event_ids (explicabilidade)")
        if self.event_id in self.related_event_ids:
            raise ValueError("related_event_ids não pode conter o próprio event_id")
        if self.window is not None and not (self.window.start <= self.timestamp <= self.window.end):
            raise ValueError("timestamp precisa estar dentro de window")
        tamanho = len(self.model_dump_json().encode("utf-8"))
        if tamanho > TAMANHO_MAX_BYTES:
            raise ValueError(
                f"evento com {tamanho} bytes excede {TAMANHO_MAX_BYTES}; grave a evidência em arquivo e referencie a URI"
            )
        return self

    # ----------------------------------------------------------------------- #
    # API de conveniência
    # ----------------------------------------------------------------------- #

    @classmethod
    def criar(cls, **campos: Any) -> "EventoAchado":
        """Cria um evento preenchendo ``event_id`` e ``detected_at`` automaticamente."""
        campos.setdefault("event_id", uuid.uuid4())
        campos.setdefault("detected_at", agora_utc())
        return cls.model_validate(campos)

    @classmethod
    def de_json(cls, dados: str | bytes) -> "EventoAchado":
        return cls.model_validate_json(dados)

    def para_json(self) -> str:
        return self.model_dump_json(exclude_none=True)

    def para_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

    @property
    def alertavel(self) -> bool:
        """Achado que o motor de alertas (US19) deve considerar."""
        return self.severity.peso >= Severidade.MEDIA.peso


__all__ = [
    "Artefato", "Contexto", "Evidencia", "EventoAchado", "Janela",
    "SCHEMA_VERSION", "TAMANHO_MAX_BYTES", "ValidationError", "agora_utc",
]
