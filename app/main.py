from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

from app.routers import sentiment, transcription, video_areas, video_pose  # noqa: E402

app = FastAPI(
    title="Monitoramento Médico Multimodal - API",
    description="Endpoints locais para testar os fluxos Azure Speech e Language e a análise de pose em vídeo.",
)

app.include_router(transcription.router)
app.include_router(sentiment.router)
app.include_router(video_pose.router)
app.include_router(video_areas.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
