import asyncio
import contextlib
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

from alertas import ConfigAlertas, construir  # noqa: E402
from app.barramento import get_barramento  # noqa: E402
from app.routers import (  # noqa: E402
    alertas,
    audio_analysis,
    sentiment,
    text_analysis,
    transcription,
    video_areas,
    video_pose,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Liga o motor de alertas ao barramento e roda o worker enquanto o app estiver de pé."""
    servico = construir(ConfigAlertas.from_env())
    servico.assinar(get_barramento())
    app.state.alertas = servico
    worker = asyncio.create_task(servico.executar())
    try:
        yield
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker


app = FastAPI(
    title="Monitoramento Médico Multimodal - API",
    description=(
        "Endpoints locais para testar os fluxos Azure Speech e Language, a transcrição de "
        "consultas (US11), os termos críticos e o sentimento na fala do paciente (US13), a "
        "análise de pose em vídeo e os alertas automáticos para a equipe médica (US19)."
    ),
    lifespan=lifespan,
)

app.include_router(transcription.router)
app.include_router(sentiment.router)
app.include_router(text_analysis.router)
app.include_router(audio_analysis.router)
app.include_router(video_pose.router)
app.include_router(video_areas.router)
app.include_router(alertas.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
