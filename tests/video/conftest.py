"""Fixtures da US07 (detecção de objetos e áreas críticas)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from contratos import EmissorMemoria, pseudonimizar_paciente
from video import (
    AreaCritica,
    Caixa,
    Configuracao,
    ConjuntoAreas,
    Deteccao,
    LeitorVideo,
    MonitorAreas,
    criar_video_teste,
)

CHAVE_TESTE = "chave-de-teste-com-mais-de-32-bytes-000000"
PACIENTE = pseudonimizar_paciente(10000032, chave=CHAVE_TESTE)


@pytest.fixture(autouse=True)
def _chave_pseudonimizacao(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSEUDONYM_KEY", CHAVE_TESTE)


@pytest.fixture
def quadrado() -> AreaCritica:
    return AreaCritica(
        id="teste",
        nome="Área de teste",
        pontos=((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)),
    )


@pytest.fixture
def areas(quadrado: AreaCritica) -> ConjuntoAreas:
    return ConjuntoAreas((quadrado,))


def deteccao(
    caixa: Caixa,
    *,
    frame_index: int = 0,
    tempo_s: float = 0.0,
    track_id: int | None = 7,
    classe: str = "person",
    class_id: int = 0,
    confianca: float = 0.9,
) -> Deteccao:
    return Deteccao(
        class_id=class_id,
        class_name=classe,
        confianca=confianca,
        caixa=caixa,
        frame_index=frame_index,
        tempo_s=tempo_s,
        track_id=track_id,
    )


@pytest.fixture
def dentro() -> Deteccao:
    return deteccao(Caixa(10, 10, 90, 90))


@pytest.fixture
def fora() -> Deteccao:
    return deteccao(Caixa(300, 300, 380, 380))


@pytest.fixture
def monitor(areas: ConjuntoAreas) -> MonitorAreas:
    return MonitorAreas(areas=areas, criterio="fracao", persistencia_quadros=2)


@pytest.fixture
def video_sintetico(tmp_path: Path) -> Path:
    return criar_video_teste(tmp_path / "entrada.avi", quadros=20, fps=10.0)


@pytest.fixture
def config_base(video_sintetico: Path, areas: ConjuntoAreas) -> Configuracao:
    return Configuracao.de_dict(
        {
            "fonte": {
                "video": str(video_sintetico),
                "id": "cam-test",
                "patient_id": PACIENTE,
                "bed_id": "UTI-01",
                "procedure_type": "fisioterapia",
                "inicio_utc": "2026-10-05T19:00:00Z",
            },
            "detector": {"pesos": "yolov8n.pt", "confianca": 0.25, "iou": 0.5},
            "areas": [
                {
                    **area.para_dict(),
                    "largura_ref": 320,
                    "altura_ref": 240,
                }
                for area in areas
            ],
            "eventos": {"severidade": "baixa", "emitir_jsonl": False},
        }
    )


@pytest.fixture
def emissor() -> EmissorMemoria:
    return EmissorMemoria()


@pytest.fixture
def leitor(video_sintetico: Path) -> Iterator[LeitorVideo]:
    with LeitorVideo(video_sintetico) as aberto:
        yield aberto
