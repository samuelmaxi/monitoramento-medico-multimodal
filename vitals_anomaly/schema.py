"""Schema canônico das séries de sinais vitais (formato *wide*).

Uma linha por (``patient_id``, ``timestamp``) e uma coluna por sinal. Todo
adapter de fonte (eICU, sintético, streaming) entrega exatamente este formato,
validado por :func:`validate_vitals`.

Unidades canônicas:

========================  =========  ==========================================
coluna                    unidade    observação
========================  =========  ==========================================
``hr``                    bpm        frequência cardíaca
``sbp`` / ``dbp``         mmHg       pressão arterial sistólica / diastólica
``spo2``                  %          saturação periférica de O2 (0-100)
``rr``                    irpm       frequência respiratória (opcional)
``temp``                  °C         temperatura (opcional)
``o2_supplemental``       bool       O2 suplementar (opcional, NEWS2)
``consciousness``         A/C/V/P/U  nível de consciência ACVPU (opcional, NEWS2)
``bp_source``             —          ``invasive``/``noninvasive`` (opcional)
========================  =========  ==========================================

Sinais ausentes são ``NaN`` (numéricos) ou ``<NA>`` (booleano/categórico); a
ingestão nunca preenche valores, para não introduzir informação do futuro.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import pandas as pd
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

ID_COLUMNS: tuple[str, ...] = ("patient_id", "timestamp")
REQUIRED_VITALS: tuple[str, ...] = ("hr", "sbp", "dbp", "spo2")
OPTIONAL_VITALS: tuple[str, ...] = ("rr", "temp")
NUMERIC_VITALS: tuple[str, ...] = REQUIRED_VITALS + OPTIONAL_VITALS
CONTEXT_COLUMNS: tuple[str, ...] = ("o2_supplemental", "consciousness", "bp_source")
CANONICAL_COLUMNS: tuple[str, ...] = ID_COLUMNS + NUMERIC_VITALS + CONTEXT_COLUMNS


class Consciousness(str, Enum):
    """Escala ACVPU usada no NEWS2."""

    ALERT = "A"
    CONFUSION = "C"
    VOICE = "V"
    PAIN = "P"
    UNRESPONSIVE = "U"


class BPSource(str, Enum):
    """Origem da medida de pressão arterial."""

    INVASIVE = "invasive"
    NONINVASIVE = "noninvasive"


CONSCIOUSNESS_DTYPE = pd.CategoricalDtype([c.value for c in Consciousness])
BP_SOURCE_DTYPE = pd.CategoricalDtype([b.value for b in BPSource])
TIMESTAMP_DTYPE = pd.DatetimeTZDtype(unit="ns", tz="UTC")

CANONICAL_DTYPES: dict[str, Any] = {
    "patient_id": pd.StringDtype(),
    "timestamp": TIMESTAMP_DTYPE,
    **{c: "float64" for c in NUMERIC_VITALS},
    "o2_supplemental": pd.BooleanDtype(),
    "consciousness": CONSCIOUSNESS_DTYPE,
    "bp_source": BP_SOURCE_DTYPE,
}


class SchemaError(ValueError):
    """DataFrame fora do schema canônico."""


class VitalSample(BaseModel):
    """Uma amostra de sinais vitais de um paciente, para o modo streaming.

    Os limites dos campos são só de validade física (um valor negativo ou uma
    SpO2 acima de 100% são erro de ingestão); faixas clínicas ficam no config.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    patient_id: str = Field(min_length=1)
    timestamp: AwareDatetime
    hr: float | None = Field(default=None, ge=0)
    sbp: float | None = Field(default=None, ge=0)
    dbp: float | None = Field(default=None, ge=0)
    spo2: float | None = Field(default=None, ge=0, le=100)
    rr: float | None = Field(default=None, ge=0)
    temp: float | None = Field(default=None, gt=0)
    o2_supplemental: bool | None = None
    consciousness: Consciousness | None = None
    bp_source: BPSource | None = None

    @field_validator("timestamp")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    def to_row(self) -> dict[str, Any]:
        """Dicionário com as colunas canônicas (enums viram seus valores)."""
        return self.model_dump(mode="python") | {
            "consciousness": self.consciousness.value if self.consciousness else None,
            "bp_source": self.bp_source.value if self.bp_source else None,
        }


def empty_vitals_frame() -> pd.DataFrame:
    """DataFrame vazio já com as colunas e dtypes canônicos."""
    return pd.DataFrame({c: pd.Series(dtype=t) for c, t in CANONICAL_DTYPES.items()})


def coerce_vitals(df: pd.DataFrame) -> pd.DataFrame:
    """Completa colunas opcionais ausentes, aplica os dtypes canônicos e ordena.

    Colunas obrigatórias (ids e sinais de :data:`REQUIRED_VITALS`) precisam
    existir; as opcionais são criadas vazias. Colunas fora do schema geram erro
    para não passarem despercebidas.
    """
    faltando = [c for c in ID_COLUMNS + REQUIRED_VITALS if c not in df.columns]
    if faltando:
        raise SchemaError(f"colunas obrigatórias ausentes: {faltando}")
    extras = [c for c in df.columns if c not in CANONICAL_DTYPES]
    if extras:
        raise SchemaError(f"colunas fora do schema canônico: {extras}")

    out = pd.DataFrame(index=df.index)
    for coluna, dtype in CANONICAL_DTYPES.items():
        if coluna not in df.columns:
            out[coluna] = pd.Series(pd.NA if dtype != "float64" else float("nan"), index=df.index, dtype=dtype)
        elif coluna == "timestamp":
            out[coluna] = pd.to_datetime(df[coluna], utc=True).astype(TIMESTAMP_DTYPE)
        else:
            out[coluna] = df[coluna].astype(dtype)
    return out.sort_values(list(ID_COLUMNS), kind="stable").reset_index(drop=True)


def validate_vitals(df: pd.DataFrame) -> pd.DataFrame:
    """Confere colunas, dtypes, ordenação e unicidade; devolve o próprio ``df``.

    Raises:
        SchemaError: na primeira violação encontrada.
    """
    if list(df.columns) != list(CANONICAL_COLUMNS):
        raise SchemaError(f"colunas esperadas {list(CANONICAL_COLUMNS)}, recebidas {list(df.columns)}")
    for coluna, dtype in CANONICAL_DTYPES.items():
        if df[coluna].dtype != dtype:
            raise SchemaError(f"coluna '{coluna}' com dtype {df[coluna].dtype}, esperado {dtype}")
    if df["patient_id"].isna().any() or df["timestamp"].isna().any():
        raise SchemaError("patient_id e timestamp não podem ser nulos")
    if df.duplicated(list(ID_COLUMNS)).any():
        raise SchemaError("há linhas duplicadas para o mesmo (patient_id, timestamp)")
    chave = pd.MultiIndex.from_frame(df[list(ID_COLUMNS)])
    if not chave.is_monotonic_increasing:
        raise SchemaError("o DataFrame deve estar ordenado por patient_id e timestamp")
    if (df["spo2"] > 100).any():
        raise SchemaError("spo2 acima de 100%")
    return df


def samples_to_frame(samples: Iterable[VitalSample]) -> pd.DataFrame:
    """Converte amostras de streaming para o DataFrame canônico."""
    linhas = [s.to_row() for s in samples]
    if not linhas:
        return empty_vitals_frame()
    return validate_vitals(coerce_vitals(pd.DataFrame(linhas)))


def frame_to_samples(df: pd.DataFrame) -> list[VitalSample]:
    """Converte o DataFrame canônico em amostras, na ordem das linhas."""
    validate_vitals(df)
    registros = df.astype(object).where(df.notna(), None).to_dict(orient="records")
    return [VitalSample(**r) for r in registros]
