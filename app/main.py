from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

from app.routers import sentiment, transcription  # noqa: E402

app = FastAPI(
    title="Monitoramento Médico Multimodal - API",
    description="Endpoints locais para testar os fluxos AWS Transcribe e Comprehend.",
)

app.include_router(transcription.router)
app.include_router(sentiment.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
