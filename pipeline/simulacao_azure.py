"""Substituto determinístico do SDK do Azure AI Language para o modo simulado (US20).

Devolve objetos no mesmo formato do ``TextAnalyticsClient`` (SDK 5.3.0), para que
``LanguageClient`` e ``analyze_transcript`` rodem sem chaves nem rede.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from audio.text.segmentation import sentence_spans

Regras = dict[str, dict[str, Any]]

REGRAS_CONSULTA_DEMO: Regras = {
    "falta de ar": {"category": "SymptomOrSign", "certainty": "positive", "confidence": 0.98},
    "aperto no peito": {"category": "SymptomOrSign", "certainty": "positive", "confidence": 0.95},
}
PALAVRAS_NEGATIVAS_DEMO = ("cansado", "falta de ar", "aperto")


def _entidade(texto_doc: str, trecho: str, attrs: dict[str, Any]) -> SimpleNamespace:
    offset = texto_doc.casefold().index(trecho.casefold())
    assertion = SimpleNamespace(
        certainty=attrs.get("certainty"),
        conditionality=attrs.get("conditionality"),
        association=attrs.get("association"),
        temporality=attrs.get("temporality"),
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


class LanguageSdkSimulado:
    """Imita ``begin_analyze_healthcare_entities`` e ``analyze_sentiment``."""

    def __init__(
        self,
        regras: Regras | None = None,
        palavras_negativas: tuple[str, ...] = PALAVRAS_NEGATIVAS_DEMO,
    ) -> None:
        self.regras = REGRAS_CONSULTA_DEMO if regras is None else regras
        self.palavras_negativas = palavras_negativas

    def begin_analyze_healthcare_entities(self, documents, *, language, model_version, raw_response_hook=None):
        docs = []
        for d in documents:
            entidades = [
                _entidade(d["text"], trecho, attrs)
                for trecho, attrs in self.regras.items()
                if trecho.casefold() in d["text"].casefold()
            ]
            docs.append(SimpleNamespace(id=d["id"], is_error=False, entities=entidades))
        if raw_response_hook:
            raw_response_hook(SimpleNamespace(raw_response={
                "status": "succeeded",
                "tasks": {"items": [{"results": {"modelVersion": "2023-04-28", "documents": []}}]},
            }))
        return SimpleNamespace(wait=lambda timeout=None: None, done=lambda: True, result=lambda: iter(docs))

    def analyze_sentiment(self, documents, *, language, model_version, raw_response_hook=None):
        docs = []
        for d in documents:
            sentencas = []
            for a, b in sentence_spans(d["text"]):
                texto = d["text"][a:b]
                negativa = any(p in texto.casefold() for p in self.palavras_negativas)
                pos, neu, neg = (0.02, 0.08, 0.90) if negativa else (0.10, 0.85, 0.05)
                sentencas.append(SimpleNamespace(
                    text=texto,
                    sentiment="negative" if negativa else "neutral",
                    confidence_scores=SimpleNamespace(positive=pos, neutral=neu, negative=neg),
                    offset=a,
                    length=b - a,
                ))
            docs.append(SimpleNamespace(id=d["id"], is_error=False, sentences=sentencas))
        if raw_response_hook:
            raw_response_hook(SimpleNamespace(model_version="2025-01-01"))
        return docs
