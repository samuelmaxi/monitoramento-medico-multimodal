"""Fixtures da US13: um ``TextAnalyticsClient`` falso com respostas no formato do SDK."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

import pytest

from audio.text.config import TextConfig, load_config
from audio.text.language_client import LanguageClient
from audio.text.segmentation import sentence_spans
from audio.text.transcript import transcript_from_dict

CONFIG_PATH = Path(__file__).resolve().parents[1] / "text" / "config.yaml"
CHAVE_TESTE = "chave-de-teste-us13-com-mais-de-32-bytes!!"

# Regras do serviço falso: trecho → atributos da entidade. As asserções são as
# que o Text Analytics for health devolveria para cada frase do teste.
Regras = dict[str, dict[str, Any]]


@pytest.fixture(autouse=True)
def _chave(monkeypatch):
    monkeypatch.setenv("PSEUDONYM_KEY", CHAVE_TESTE)


@pytest.fixture
def config() -> TextConfig:
    """Config real do módulo, sem cache em disco."""
    cfg = load_config(CONFIG_PATH)
    return dataclasses.replace(cfg, azure=dataclasses.replace(cfg.azure, cache_dir=None))


def _entidade(texto_doc: str, trecho: str, attrs: dict[str, Any]) -> SimpleNamespace:
    offset = texto_doc.casefold().index(trecho.casefold())
    assertion = None
    if any(k in attrs for k in ("certainty", "conditionality", "association", "temporality")):
        assertion = SimpleNamespace(
            certainty=attrs.get("certainty"),
            conditionality=attrs.get("conditionality"),
            association=attrs.get("association"),
            **({"temporality": attrs["temporality"]} if "temporality" in attrs else {}),
        )
    return SimpleNamespace(
        text=texto_doc[offset:offset + len(trecho)],
        category=attrs.get("category", "SymptomOrSign"),
        subcategory=None,
        confidence_score=attrs.get("confidence", 0.95),
        offset=offset,
        length=len(trecho),
        normalized_text=attrs.get("normalized"),
        assertion=assertion,
    )


class FakeTextAnalytics:
    """Imita ``begin_analyze_healthcare_entities`` e ``analyze_sentiment`` do SDK 5.3.0."""

    def __init__(
        self,
        regras: Optional[Regras] = None,
        palavras_negativas: tuple[str, ...] = ("medo", "triste", "mal", "péssima", "cansada", "inútil"),
        falhas: Optional[list[Exception]] = None,
        jobs_parados: int = 0,
    ) -> None:
        self.regras = regras or {}
        self.palavras_negativas = palavras_negativas
        self.falhas = list(falhas or [])
        # Quantos jobs do health ficam "na fila" sem terminar (done() == False).
        self.jobs_parados = jobs_parados
        self.health_waits: list[float] = []
        self.health_calls: list[dict[str, Any]] = []
        self.sentiment_calls: list[dict[str, Any]] = []

    def _talvez_falhar(self) -> None:
        if self.falhas:
            raise self.falhas.pop(0)

    def begin_analyze_healthcare_entities(self, documents, *, language, model_version, raw_response_hook=None):
        self._talvez_falhar()
        self.health_calls.append({"documents": documents, "language": language})
        docs = []
        for d in documents:
            entidades = [_entidade(d["text"], trecho, attrs) for trecho, attrs in self.regras.items()
                         if trecho.casefold() in d["text"].casefold()]
            docs.append(SimpleNamespace(id=d["id"], is_error=False, entities=entidades))
        if raw_response_hook:
            raw_response_hook(SimpleNamespace(raw_response={
                "status": "succeeded",
                "tasks": {"items": [{"results": {"modelVersion": "2023-04-28", "documents": []}}]},
            }))
        parado = self.jobs_parados > 0
        self.jobs_parados -= parado
        return SimpleNamespace(
            wait=lambda timeout=None: self.health_waits.append(timeout),
            done=lambda: not parado,
            result=lambda: iter(docs),
        )

    def analyze_sentiment(self, documents, *, language, model_version, raw_response_hook=None):
        self._talvez_falhar()
        self.sentiment_calls.append({"documents": documents, "language": language})
        docs = []
        for d in documents:
            sentencas = []
            for a, b in sentence_spans(d["text"]):
                texto = d["text"][a:b]
                negativa = any(p in texto.casefold() for p in self.palavras_negativas)
                pos, neu, neg = (0.02, 0.08, 0.90) if negativa else (0.10, 0.85, 0.05)
                sentencas.append(SimpleNamespace(
                    text=texto, sentiment="negative" if negativa else "neutral",
                    confidence_scores=SimpleNamespace(positive=pos, neutral=neu, negative=neg),
                    offset=a, length=b - a,
                ))
            docs.append(SimpleNamespace(id=d["id"], is_error=False, sentences=sentencas))
        if raw_response_hook:
            raw_response_hook(SimpleNamespace(model_version="2025-01-01"))
        return docs


@pytest.fixture
def fake_factory(config) -> Callable[..., tuple[FakeTextAnalytics, LanguageClient]]:
    def criar(regras: Optional[Regras] = None, **kwargs) -> tuple[FakeTextAnalytics, LanguageClient]:
        fake = FakeTextAnalytics(regras, **kwargs)
        esperas: list[float] = []
        cliente = LanguageClient(fake, config.azure, sleep=esperas.append)
        cliente.esperas = esperas  # type: ignore[attr-defined]
        return fake, cliente
    return criar


def consulta(*falas: tuple[str, str], audio_id: str = "consulta-teste", source: Optional[Path] = None):
    """Transcrição no formato da US11 a partir de pares (falante, texto)."""
    segmentos, offset = [], 0
    for falante, texto in falas:
        duracao = max(1000, len(texto) * 60)
        segmentos.append({"speaker": falante, "text": texto, "offset_ms": offset,
                          "duration_ms": duracao, "confidence": 0.9})
        offset += duracao + 300
    return transcript_from_dict({
        "audio_id": audio_id,
        "patient_id": "sim-paciente-teste",
        "recorded_at": "2026-10-01T13:00:00Z",
        "is_synthetic": True,
        "segments": segmentos,
    }, source=source)
