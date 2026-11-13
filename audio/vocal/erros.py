"""Erros da análise vocal. ``ErroDeAnalise`` é a falha esperada de uma entrada inanalisável."""

from __future__ import annotations


class ErroDeAnalise(ValueError):
    """Entrada que não pode ser analisada (a API responde 4xx; a CLI registra e segue)."""


class TranscricaoInvalida(ErroDeAnalise):
    """JSON de transcrição (US11) sem falas ou sem como separar paciente e médico."""


class SemFalaDoPaciente(ErroDeAnalise):
    """Nenhum segmento de fala do paciente com duração e energia suficientes para medir."""
