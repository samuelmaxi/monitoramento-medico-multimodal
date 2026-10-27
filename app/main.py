from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

from app.routers import (  # noqa: E402
    audio_analysis,
    sentiment,
    text_analysis,
    transcription,
    video_areas,
    video_pose,
)

app = FastAPI(
    title="Monitoramento Médico Multimodal - API",
    description=(
        "Endpoints locais para testar os fluxos Azure Speech e Language, a transcrição de "
        "consultas (US11), os termos críticos e o sentimento na fala do paciente (US13) e a "
        "análise de pose em vídeo."
    ),
)

app.include_router(transcription.router)
app.include_router(sentiment.router)
app.include_router(text_analysis.router)
app.include_router(audio_analysis.router)
app.include_router(video_pose.router)
app.include_router(video_areas.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
