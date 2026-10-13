from azure.core.exceptions import AzureError
from fastapi import APIRouter

from app.azure_clients import get_language_client
from app.errors import translate_client_error
from app.schemas import SentimentRequest, SentimentResponse

router = APIRouter()


@router.post("/sentiment", response_model=SentimentResponse)
def detect_sentiment(payload: SentimentRequest) -> SentimentResponse:
    try:
        client = get_language_client()
        language_code = _map_language_code(payload.language_code)

        result = client.analyze_sentiment(
            documents=[payload.text],
            language=language_code
        )

        sentiment_result = result[0].sentiment
        sentiment_scores = result[0].confidence_scores

        return SentimentResponse(
            sentiment=sentiment_result.upper(),
            sentiment_score={
                "positive": sentiment_scores.positive,
                "neutral": sentiment_scores.neutral,
                "negative": sentiment_scores.negative,
            },
        )
    except AzureError as exc:
        raise translate_client_error(exc) from exc


def _map_language_code(code: str) -> str:
    """Map language codes to Azure's format"""
    mapping = {
        "pt": "pt-BR",
        "pt-BR": "pt-BR",
        "pt-PT": "pt-PT",
        "en": "en-US",
        "en-US": "en-US",
    }
    return mapping.get(code, code)
