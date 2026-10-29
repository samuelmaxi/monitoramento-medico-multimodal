"""US15 — detecção de anomalias em séries temporais de sinais vitais.

Decisões de projeto em ``docs/US15_decisoes.md``.
"""

from .data import load_vitals
from .schema import CANONICAL_COLUMNS, VitalSample, validate_vitals

__all__ = ["CANONICAL_COLUMNS", "VitalSample", "load_vitals", "validate_vitals"]
