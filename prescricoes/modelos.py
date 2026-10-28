"""Prescrição normalizada, internação e anomalia detectada."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from contratos import Severidade

from .normalizacao import Dose


@dataclass(frozen=True)
class Prescricao:
    """Uma linha de prescrição já normalizada (unidade canônica, via, frequência)."""

    ordem_id: str
    paciente: str
    """ID de origem (``subject_id``); só vira pseudônimo na emissão do evento."""
    internacao: str | None
    medicamento: str
    principios: tuple[str, ...]
    dose: Dose | None
    via: str
    """Via como veio (``PO/NG``, ``IV DRIP``)."""
    via_grupo: str
    """Via agrupada (``enteral``, ``IV``) usada nas comparações."""
    frequencia: str | None
    tipo_frequencia: str
    doses_24h: float | None
    inicio: datetime
    fim: datetime | None
    dose_texto: str = ""
    unidade_texto: str = ""
    apresentacao: str = ""
    status: str | None = None
    sintetica: bool = False
    """Linha injetada pela avaliação (nunca vem do MIMIC)."""

    @property
    def dose_diaria(self) -> float | None:
        if self.dose is None or not self.doses_24h:
            return None
        return self.dose.valor * self.doses_24h

    def ativa_em(self, instante: datetime) -> bool:
        return self.inicio <= instante and (self.fim is None or instante < self.fim)

    def resumo(self) -> dict[str, Any]:
        """O que vai em ``antes``/``depois`` na justificativa do evento."""
        return {
            "medicamento": self.medicamento,
            "dose": None if self.dose is None else round(self.dose.valor, 4),
            "unidade": None if self.dose is None else self.dose.unidade,
            "dose_original": f"{self.dose_texto} {self.unidade_texto}".strip(),
            "via": self.via,
            "frequencia": self.frequencia,
            "dose_diaria": None if self.dose_diaria is None else round(self.dose_diaria, 4),
            "inicio": self.inicio.isoformat(),
            "fim": None if self.fim is None else self.fim.isoformat(),
        }


@dataclass(frozen=True)
class Internacao:
    id: str | None
    """``hadm_id``; ``None`` quando a API recebe ordens sem a internação."""
    paciente: str
    admissao: datetime
    alta: datetime | None
    obito: datetime | None = None


@dataclass(frozen=True)
class Anomalia:
    event_type: str
    regra: str
    severidade: Severidade
    score: float
    paciente: str
    internacao: str | None
    instante: datetime
    inicio: datetime
    fim: datetime
    principio: str
    resumo: str
    """Justificativa legível: a regra que disparou e os valores antes × depois."""
    antes: Mapping[str, Any] | None = None
    depois: Mapping[str, Any] | None = None
    features: Mapping[str, Any] = field(default_factory=dict)
    limiares: Mapping[str, Any] = field(default_factory=dict)
    ordens: tuple[str, ...] = ()
    """``ordem_id`` das prescrições envolvidas (rastreio e avaliação)."""
