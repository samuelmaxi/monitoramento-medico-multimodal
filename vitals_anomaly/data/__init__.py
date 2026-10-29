"""Ingestão de sinais vitais: adapters de fonte e o ponto de entrada ``load_vitals``."""

from .loader import SOURCES, load_vitals

__all__ = ["SOURCES", "load_vitals"]
