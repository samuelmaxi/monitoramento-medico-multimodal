import asyncio
import contextlib
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

from alertas import ConfigAlertas, construir  # noqa: E402
from app.barramento import get_barramento  # noqa: E402
from app.routers import (  # noqa: E402
    alertas,
    audio_analysis,
    audio_vocal,
    fusao,
    prescricoes,
    sentiment,
    sinais_vitais,
    text_analysis,
    transcription,
    video_areas,
    video_pose,
)
from fusao import CAMINHO_PADRAO as CONFIG_FUSAO  # noqa: E402
from fusao import FusaoMultimodal, carregar_config  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Liga a fusão (US18) e o motor de alertas (US19) ao barramento e roda o worker."""
    barramento = get_barramento()
    config_fusao = carregar_config(os.getenv("FUSAO_CONFIG_PATH") or CONFIG_FUSAO)
    fusao_multimodal = FusaoMultimodal(config_fusao)
    fusao_multimodal.assinar(barramento)
    app.state.fusao = fusao_multimodal
    servico = construir(ConfigAlertas.from_env())
    servico.assinar(barramento)
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
        "consultas (US11), os termos críticos e o sentimento na fala do paciente (US13), os "
        "biomarcadores vocais e achados de áudio (US10, US12, US14), a análise de pose em vídeo "
        "e os alertas automáticos para a equipe médica (US19)."
        "consultas (US11), os termos críticos e o sentimento na fala do paciente (US13), a "
        "análise de pose em vídeo, a fusão multimodal em score de risco por paciente (US18) e os "
        "alertas automáticos para a equipe médica (US19)."
    ),
    lifespan=lifespan,
)

app.include_router(transcription.router)
app.include_router(sentiment.router)
app.include_router(text_analysis.router)
app.include_router(audio_analysis.router)
app.include_router(audio_vocal.router)
app.include_router(video_pose.router)
app.include_router(video_areas.router)
app.include_router(alertas.router)
app.include_router(sinais_vitais.router)
app.include_router(prescricoes.router)
app.include_router(fusao.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
