"""Leitura das transcrições diarizadas da US11 (``outputs/transcripts/<audio_id>.json``).

Formato esperado::

    {
      "audio_id": "consulta-01",               # vira context.source_id e o nome da saída
      "patient_id": "sim-paciente-01",          # ID de origem; pseudonimizado na emissão
      "recorded_at": "2026-10-01T13:00:00Z",    # início da gravação, UTC
      "is_synthetic": true,                     # opcional (padrão false)
      "segments": [
        {"speaker": "medico", "text": "...", "offset_ms": 0, "duration_ms": 2400, "confidence": 0.94}
      ]
    }

``recorded_at`` + ``offset_ms`` dá o ``timestamp`` UTC do evento, que o contrato
da US04 exige; por isso ``recorded_at`` e ``patient_id`` são obrigatórios aqui.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# Mesmo padrão de context.source_id no contrato: o audio_id vira source_id.
from contratos.evento import PADRAO_ID_CURTO


class TranscriptError(ValueError):
    """Transcrição fora do formato esperado."""


@dataclass(frozen=True)
class Segment:
    index: int
    speaker: str
    text: str
    offset_ms: int
    duration_ms: int
    confidence: Optional[float]

    @property
    def end_ms(self) -> int:
        return self.offset_ms + self.duration_ms


@dataclass(frozen=True)
class Transcript:
    audio_id: str
    patient_id: str
    recorded_at: datetime
    segments: tuple[Segment, ...]
    is_synthetic: bool = False
    source: Optional[Path] = None

    def instant(self, offset_ms: int) -> datetime:
        """Instante UTC de um ponto da gravação."""
        return self.recorded_at + timedelta(milliseconds=offset_ms)


def _exigir(dados: dict[str, Any], chave: str, origem: str) -> Any:
    if chave not in dados:
        raise TranscriptError(f"{origem}: campo obrigatório ausente: '{chave}'")
    return dados[chave]


def _utc(valor: Any, origem: str) -> datetime:
    if not isinstance(valor, str):
        raise TranscriptError(f"{origem}: 'recorded_at' deve ser texto ISO-8601 UTC")
    try:
        instante = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        raise TranscriptError(f"{origem}: 'recorded_at' inválido: {valor!r}") from None
    if instante.tzinfo is None or instante.utcoffset() != timedelta(0):
        raise TranscriptError(f"{origem}: 'recorded_at' precisa estar em UTC (sufixo Z)")
    return instante.astimezone(timezone.utc)


def _inteiro_nao_negativo(valor: Any, campo: str, origem: str) -> int:
    if not isinstance(valor, int) or isinstance(valor, bool) or valor < 0:
        raise TranscriptError(f"{origem}: '{campo}' deve ser inteiro >= 0 (recebido: {valor!r})")
    return valor


def transcript_from_dict(dados: dict[str, Any], source: Optional[Path] = None) -> Transcript:
    origem = str(source) if source else "transcrição"
    if not isinstance(dados, dict):
        raise TranscriptError(f"{origem}: esperado um objeto JSON")

    audio_id = str(_exigir(dados, "audio_id", origem))
    if not re.fullmatch(PADRAO_ID_CURTO, audio_id):
        raise TranscriptError(f"{origem}: 'audio_id' deve casar {PADRAO_ID_CURTO} (sem espaços nem dados pessoais)")

    brutos = _exigir(dados, "segments", origem)
    if not isinstance(brutos, list):
        raise TranscriptError(f"{origem}: 'segments' deve ser uma lista")
    segmentos = []
    for i, bruto in enumerate(brutos):
        local = f"{origem} segments[{i}]"
        if not isinstance(bruto, dict):
            raise TranscriptError(f"{local}: esperado um objeto")
        texto = _exigir(bruto, "text", local)
        if not isinstance(texto, str):
            raise TranscriptError(f"{local}: 'text' deve ser texto")
        confianca = bruto.get("confidence")
        segmentos.append(Segment(
            index=i,
            speaker=str(_exigir(bruto, "speaker", local)),
            text=texto,
            offset_ms=_inteiro_nao_negativo(_exigir(bruto, "offset_ms", local), "offset_ms", local),
            duration_ms=_inteiro_nao_negativo(_exigir(bruto, "duration_ms", local), "duration_ms", local),
            confidence=float(confianca) if confianca is not None else None,
        ))

    return Transcript(
        audio_id=audio_id,
        patient_id=str(_exigir(dados, "patient_id", origem)),
        recorded_at=_utc(_exigir(dados, "recorded_at", origem), origem),
        segments=tuple(segmentos),
        is_synthetic=bool(dados.get("is_synthetic", False)),
        source=source,
    )


def load_transcript(path: str | Path) -> Transcript:
    path = Path(path)
    try:
        dados = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TranscriptError(f"{path}: JSON inválido: {exc}") from None
    return transcript_from_dict(dados, source=path)
