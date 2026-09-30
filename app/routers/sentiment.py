from botocore.exceptions import ClientError
from fastapi import APIRouter

from app.aws_clients import get_comprehend_client
from app.errors import translate_client_error
from app.schemas import SentimentRequest, SentimentResponse

router = APIRouter()


@router.post("/sentiment", response_model=SentimentResponse)
def detect_sentiment(payload: SentimentRequest) -> SentimentResponse:
    try:
        result = get_comprehend_client().detect_sentiment(
            Text=payload.text, LanguageCode=payload.language_code
        )
    except ClientError as exc:
        raise translate_client_error(exc) from exc

    return SentimentResponse(
        sentiment=result["Sentiment"], sentiment_score=result["SentimentScore"]
    )
