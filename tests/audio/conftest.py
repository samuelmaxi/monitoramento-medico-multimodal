"""Fixtures dos testes de áudio (US10/US12/US14): todo áudio é sintético e vive em ``tmp_path``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from audio.ingestao.config import ConfigIngestao, carregar_config
from audio.vocal.config import ConfigVocal
from audio.vocal.config import carregar_config as carregar_config_vocal

RAIZ = Path(__file__).resolve().parents[2]
CHAVE_TESTE = "chave-de-teste-com-mais-de-32-bytes-000000"


@pytest.fixture(autouse=True)
def _chave_pseudonimizacao(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSEUDONYM_KEY", CHAVE_TESTE)


@pytest.fixture(scope="session")
def cfg_ingestao() -> ConfigIngestao:
    return carregar_config(RAIZ / "audio" / "ingestao" / "config.yaml")


@pytest.fixture(scope="session")
def cfg_vocal() -> ConfigVocal:
    return carregar_config_vocal(RAIZ / "audio" / "vocal" / "config.yaml")


def tom(duracao_s: float, taxa_hz: int = 16000, freq_hz: float = 220.0, amplitude: float = 0.5,
        canais: int = 1) -> np.ndarray:
    """Seno puro, forma ``(amostras, canais)`` em float32."""
    t = np.arange(round(duracao_s * taxa_hz)) / taxa_hz
    onda = (amplitude * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)
    return np.tile(onda[:, None], (1, canais))


def silencio(duracao_s: float, taxa_hz: int = 16000, canais: int = 1, ruido: float = 0.0,
             semente: int = 0) -> np.ndarray:
    """Silêncio (zeros) ou ruído de fundo fraco de amplitude ``ruido``."""
    n = round(duracao_s * taxa_hz)
    if ruido == 0.0:
        return np.zeros((n, canais), dtype=np.float32)
    rng = np.random.default_rng(semente)
    return (ruido * rng.standard_normal((n, canais))).astype(np.float32)


@pytest.fixture
def gravar_audio(tmp_path: Path) -> Callable[..., Path]:
    """``gravar_audio("nome.wav", sinal, taxa, subtype=None, format=None)`` → caminho do arquivo."""

    def _gravar(nome: str, sinal: np.ndarray, taxa_hz: int = 16000, *, subtype: str | None = None,
                format: str | None = None, pasta: Path | None = None) -> Path:
        destino = (pasta or tmp_path / "entrada") / nome
        destino.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(destino), sinal, taxa_hz, subtype=subtype, format=format)
        return destino

    return _gravar
