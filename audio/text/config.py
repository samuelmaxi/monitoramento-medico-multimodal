"""Leitura do ``config.yaml`` e do ``critical_terms.yaml`` da US13.

Todos os parâmetros vêm dos arquivos; o código não tem valores padrão escondidos.
Uma chave ausente gera :class:`ConfigError` com o caminho da chave, para o erro
aparecer na carga e não no meio de um lote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from contratos import Severidade

from .normalize import normalize


class ConfigError(ValueError):
    """Configuração ausente ou inválida."""


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int
    initial_delay_s: float
    max_delay_s: float


@dataclass(frozen=True)
class AzureConfig:
    health_language: str
    sentiment_language: str
    model_version: str
    max_chars_per_document: int
    sentiment_batch_size: int
    health_batch_size: int
    # Espera máxima pela operação assíncrona do health; estourou, conta como falha transitória.
    health_timeout_s: float
    retry: RetryConfig
    cache_dir: Optional[Path]


@dataclass(frozen=True)
class SpeakersConfig:
    patient: frozenset[str]
    doctor: frozenset[str]

    def role(self, speaker: str) -> Optional[str]:
        """``"paciente"``, ``"medico"`` ou ``None`` para um rótulo desconhecido."""
        rotulo = normalize(speaker)
        if rotulo in self.patient:
            return "paciente"
        if rotulo in self.doctor:
            return "medico"
        return None


@dataclass(frozen=True)
class AssertionsConfig:
    past_markers: tuple[re.Pattern[str], ...]


@dataclass(frozen=True)
class SentimentConfig:
    negative_threshold: float
    min_negative_ratio: float
    severity: Severidade


@dataclass(frozen=True)
class OutputConfig:
    dir: Path
    details_dir: Path


@dataclass(frozen=True)
class CriticalTerm:
    name: str
    synonyms: tuple[str, ...]
    normalized: tuple[str, ...]
    categories: frozenset[str]
    severity: Severidade


@dataclass(frozen=True)
class CriticalTerms:
    version: str
    terms: tuple[CriticalTerm, ...]
    source: Optional[Path] = None


@dataclass(frozen=True)
class TextConfig:
    azure: AzureConfig
    speakers: SpeakersConfig
    critical_terms: CriticalTerms
    assertions: AssertionsConfig
    sentiment: SentimentConfig
    input_extensions: tuple[str, ...]
    output: OutputConfig
    model_version: str
    source: Optional[Path] = None


def _get(data: Mapping[str, Any], path: str) -> Any:
    atual: Any = data
    for parte in path.split("."):
        if not isinstance(atual, Mapping) or parte not in atual:
            raise ConfigError(f"chave obrigatória ausente no config: '{path}'")
        atual = atual[parte]
    return atual


def _positivo(valor: Any, path: str) -> float:
    if not isinstance(valor, (int, float)) or isinstance(valor, bool) or valor <= 0:
        raise ConfigError(f"'{path}' deve ser um número positivo (recebido: {valor!r})")
    return valor


def _fracao(valor: Any, path: str) -> float:
    if not isinstance(valor, (int, float)) or isinstance(valor, bool) or not 0.0 <= valor <= 1.0:
        raise ConfigError(f"'{path}' deve estar entre 0 e 1 (recebido: {valor!r})")
    return float(valor)


def _severidade(valor: Any, path: str) -> Severidade:
    try:
        return Severidade(normalize(str(valor)))
    except ValueError:
        validas = ", ".join(s.value for s in Severidade)
        raise ConfigError(f"'{path}' deve ser uma de: {validas} (recebido: {valor!r})") from None


def _lista_de_textos(valor: Any, path: str) -> tuple[str, ...]:
    if not isinstance(valor, list) or not all(isinstance(v, str) and v.strip() for v in valor):
        raise ConfigError(f"'{path}' deve ser uma lista de textos não vazios")
    return tuple(valor)


def critical_terms_from_dict(data: Mapping[str, Any], source: Optional[Path] = None) -> CriticalTerms:
    brutos = _get(data, "terms")
    if not isinstance(brutos, list) or not 10 <= len(brutos) <= 15:
        raise ConfigError("'terms' deve ter de 10 a 15 termos críticos (DoD da US13)")
    termos = []
    for i, bruto in enumerate(brutos):
        g = lambda chave: _get(bruto, chave)  # noqa: E731
        termos.append(CriticalTerm(
            name=str(g("name")),
            synonyms=_lista_de_textos(g("synonyms"), f"terms[{i}].synonyms"),
            normalized=_lista_de_textos(g("normalized"), f"terms[{i}].normalized"),
            categories=frozenset(_lista_de_textos(g("categories"), f"terms[{i}].categories")),
            severity=_severidade(g("severity"), f"terms[{i}].severity"),
        ))
    nomes = [normalize(t.name) for t in termos]
    repetidos = sorted({n for n in nomes if nomes.count(n) > 1})
    if repetidos:
        raise ConfigError(f"termos críticos repetidos: {', '.join(repetidos)}")
    return CriticalTerms(version=str(_get(data, "version")), terms=tuple(termos), source=source)


def load_critical_terms(path: str | Path) -> CriticalTerms:
    path = Path(path)
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, Mapping):
        raise ConfigError(f"{path} não contém um mapeamento YAML")
    return critical_terms_from_dict(data, source=path)


def config_from_dict(
    data: Mapping[str, Any],
    source: Optional[Path] = None,
    critical_terms: Optional[CriticalTerms] = None,
) -> TextConfig:
    g = lambda path: _get(data, path)  # noqa: E731

    max_chars = int(_positivo(g("azure.max_chars_per_document"), "azure.max_chars_per_document"))
    if max_chars > 5120:
        raise ConfigError("'azure.max_chars_per_document' não pode passar do limite do serviço (5120)")
    sentiment_batch = int(_positivo(g("azure.sentiment_batch_size"), "azure.sentiment_batch_size"))
    if sentiment_batch > 10:
        raise ConfigError("'azure.sentiment_batch_size' não pode passar de 10 documentos por requisição")
    health_batch = int(_positivo(g("azure.health_batch_size"), "azure.health_batch_size"))
    if health_batch > 25:
        raise ConfigError("'azure.health_batch_size' não pode passar de 25 documentos por requisição")

    cache_dir = g("azure.cache_dir")
    azure = AzureConfig(
        health_language=str(g("azure.health_language")),
        sentiment_language=str(g("azure.sentiment_language")),
        model_version=str(g("azure.model_version")),
        max_chars_per_document=max_chars,
        sentiment_batch_size=sentiment_batch,
        health_batch_size=health_batch,
        health_timeout_s=float(_positivo(g("azure.health_timeout_s"), "azure.health_timeout_s")),
        retry=RetryConfig(
            max_attempts=int(_positivo(g("azure.retry.max_attempts"), "azure.retry.max_attempts")),
            initial_delay_s=float(_positivo(g("azure.retry.initial_delay_s"), "azure.retry.initial_delay_s")),
            max_delay_s=float(_positivo(g("azure.retry.max_delay_s"), "azure.retry.max_delay_s")),
        ),
        cache_dir=Path(cache_dir) if cache_dir else None,
    )

    speakers = SpeakersConfig(
        patient=frozenset(normalize(s) for s in _lista_de_textos(g("speakers.patient"), "speakers.patient")),
        doctor=frozenset(normalize(s) for s in _lista_de_textos(g("speakers.doctor"), "speakers.doctor")),
    )
    if speakers.patient & speakers.doctor:
        raise ConfigError("o mesmo rótulo de falante aparece em 'speakers.patient' e 'speakers.doctor'")

    try:
        marcadores = tuple(re.compile(p) for p in _lista_de_textos(g("assertions.past_markers"), "assertions.past_markers"))
    except re.error as exc:
        raise ConfigError(f"regex inválida em 'assertions.past_markers': {exc}") from None

    if critical_terms is None:
        caminho = Path(g("critical_terms"))
        if source is not None and not caminho.is_absolute() and not caminho.exists():
            caminho = source.parent / caminho.name
        critical_terms = load_critical_terms(caminho)

    return TextConfig(
        azure=azure,
        speakers=speakers,
        critical_terms=critical_terms,
        assertions=AssertionsConfig(past_markers=marcadores),
        sentiment=SentimentConfig(
            negative_threshold=_fracao(g("sentiment.negative_threshold"), "sentiment.negative_threshold"),
            min_negative_ratio=_fracao(g("sentiment.min_negative_ratio"), "sentiment.min_negative_ratio"),
            severity=_severidade(g("sentiment.severity"), "sentiment.severity"),
        ),
        input_extensions=tuple(e.lower() for e in _lista_de_textos(g("input.extensions"), "input.extensions")),
        output=OutputConfig(dir=Path(g("output.dir")), details_dir=Path(g("output.details_dir"))),
        model_version=str(g("event.model_version")),
        source=source,
    )


def load_config(path: str | Path) -> TextConfig:
    path = Path(path)
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, Mapping):
        raise ConfigError(f"{path} não contém um mapeamento YAML")
    return config_from_dict(data, source=path)
