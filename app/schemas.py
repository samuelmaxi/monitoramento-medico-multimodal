from pydantic import BaseModel


class TranscriptionStartResponse(BaseModel):
    job_name: str
    status: str


class TranscriptionResultResponse(BaseModel):
    status: str
    transcript: str | None = None
    failure_reason: str | None = None


class SentimentRequest(BaseModel):
    text: str
    language_code: str = "pt"


class SentimentResponse(BaseModel):
    sentiment: str
    sentiment_score: dict[str, float]
