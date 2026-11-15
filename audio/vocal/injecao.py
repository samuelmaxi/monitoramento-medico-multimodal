"""Voz sintética com alterações injetadas e rotuladas, para testes e para a avaliação da US14.

Não há conjunto público de voz patológica rotulada que o projeto possa versionar, então a
avaliação injeta, em uma voz sintética normal, as alterações que as regras procuram: jitter,
shimmer, ruído (HNR baixo), pausas, fala lenta, melodia achatada e eventos respiratórios.

Isso mede a **lógica das regras** (recall, falso positivo) sob alterações controladas. Não valida
os limiares clinicamente e não mede o YAMNet: os eventos respiratórios são injetados como
scores, não como som.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

TAXA_HZ = 16000
HARMONICOS = 20


@dataclass(frozen=True)
class ParametrosVoz:
    """Parâmetros de uma voz sintética. Os padrões são de uma voz normal e expressiva."""

    f0_hz: float = 140.0
    jitter_pct: float = 0.3
    shimmer_pct: float = 1.5
    snr_db: float = 30.0
    variacao_f0_st: float = 5.0
    """Profundidade da melodia (pico, em semitons); o desvio padrão resultante é ~0,7x isto."""
    amplitude: float = 0.3


def sintetizar_voz(
    duracao_s: float, params: ParametrosVoz, *, taxa_hz: int = TAXA_HZ, semente: int = 0
) -> np.ndarray:
    """Pulsos glóticos com ciclos de período e amplitude perturbados; devolve float32 1-D.

    Cada ciclo é uma soma de harmônicos (1/h^1,2) esticada ao período do ciclo. O jitter local
    medido por Praat fica em ~``jitter_pct`` (a diferença absoluta média de gaussianas vale
    1,128 sigma), e o ruído branco entra para o ``snr_db`` pedido.
    """
    rng = np.random.default_rng(semente)
    sigma_t = params.jitter_pct / 100.0 / 1.128
    sigma_a = params.shimmer_pct / 100.0 / 1.128

    inicios, periodos, amplitudes = [], [], []
    t = 0.0
    while t < duracao_s + 0.05:
        f0_atual = params.f0_hz * 2.0 ** (
            params.variacao_f0_st * math.sin(2 * math.pi * 0.7 * t) / 12.0)
        periodo = (1.0 / f0_atual) * (1.0 + sigma_t * rng.standard_normal())
        inicios.append(t)
        periodos.append(periodo)
        amplitudes.append(1.0 + sigma_a * rng.standard_normal())
        t += periodo
    inicios_a, periodos_a, amplitudes_a = map(np.asarray, (inicios, periodos, amplitudes))

    n = round(duracao_s * taxa_hz)
    tempo = np.arange(n) / taxa_hz
    ciclo = np.searchsorted(inicios_a, tempo, side="right") - 1
    fase = (tempo - inicios_a[ciclo]) / periodos_a[ciclo]
    h = np.arange(1, HARMONICOS + 1)
    onda = (np.sin(2 * np.pi * np.outer(fase, h)) / h**1.2).sum(axis=1) * amplitudes_a[ciclo]
    onda /= np.max(np.abs(onda))
    ruido = rng.standard_normal(n) * math.sqrt(np.mean(onda**2) / 10 ** (params.snr_db / 10))
    voz = onda + ruido
    return (params.amplitude * voz / np.max(np.abs(voz))).astype(np.float32)


@dataclass(frozen=True)
class Frase:
    duracao_s: float
    palavras: int
    pausa_depois_s: float = 0.3


def montar_fala(
    frases: Sequence[Frase],
    params: ParametrosVoz,
    *,
    taxa_hz: int = TAXA_HZ,
    semente: int = 0,
    ruido_de_fundo: float = 0.0005,
    silencio_inicial_s: float = 0.3,
) -> tuple[np.ndarray, list[dict]]:
    """Frases de voz separadas por silêncio com ruído de fundo fraco.

    Devolve ``(sinal float32 1-D, falas)``, com ``falas`` no formato de ``falas[]`` do JSON da
    US11 (``offset_ms``, ``duracao_ms`` e ``palavras``), prontas para :func:`transcricao_sintetica`.
    """
    rng = np.random.default_rng(semente + 10_000)

    def fundo(segundos: float) -> np.ndarray:
        return (ruido_de_fundo * rng.standard_normal(round(segundos * taxa_hz))).astype(np.float32)

    partes: list[np.ndarray] = [fundo(silencio_inicial_s)]
    falas: list[dict] = []
    cursor_s = silencio_inicial_s
    for i, frase in enumerate(frases):
        partes.append(sintetizar_voz(frase.duracao_s, params, taxa_hz=taxa_hz,
                                     semente=semente + i + 1))
        passo = frase.duracao_s / max(frase.palavras, 1)
        falas.append({
            "texto": " ".join(["palavra"] * frase.palavras),
            "offset_ms": round(cursor_s * 1000),
            "duracao_ms": round(frase.duracao_s * 1000),
            "palavras": [
                {"texto": "palavra", "offset_ms": round((cursor_s + k * passo) * 1000),
                 "duracao_ms": round(passo * 1000)}
                for k in range(frase.palavras)
            ],
        })
        cursor_s += frase.duracao_s
        partes.append(fundo(frase.pausa_depois_s))
        cursor_s += frase.pausa_depois_s
    return np.concatenate(partes), falas


def transcricao_sintetica(
    falas: Sequence[dict], *, audio_id: str = "sintetico", patient_id: str = "10000032",
    recorded_at: str = "2026-10-01T13:00:00Z", falante: int = 1,
    medico_falante: int | None = None,
) -> dict:
    """JSON no formato da US11 (``falas`` com falante e papel) a partir das falas sintéticas."""
    def papel(valor: int) -> str | None:
        if medico_falante is None:
            return None
        return "medico" if valor == medico_falante else "paciente"

    return {
        "schema_version": "1.1",
        "audio_id": audio_id,
        "patient_id": patient_id,
        "recorded_at": recorded_at,
        "medico_falante": medico_falante,
        "falas": [{**f, "falante": falante, "papel": papel(falante)} for f in falas],
    }


def fala_normal(*, frases: int = 4, semente: int = 0) -> tuple[np.ndarray, list[dict]]:
    """Fala normal: ~3 palavras/s, pausas curtas (~20 % do tempo)."""
    return montar_fala(
        [Frase(duracao_s=2.4, palavras=8, pausa_depois_s=0.5) for _ in range(frases)],
        ParametrosVoz(), semente=semente,
    )


# --------------------------------------------------------------------------------------------- #
# Casos rotulados para a avaliação da US14
# --------------------------------------------------------------------------------------------- #

ROTULOS = ("normal", "fadiga_vocal", "dificuldade_respiratoria", "possivel_disartria")

INDICES_YAMNET = {"Cough": 42, "Breathing": 36, "Wheeze": 37, "Gasp": 39, "Pant": 40}
"""Posição, no ``yamnet_class_map.csv``, das classes respiratórias que as regras usam."""

N_CLASSES_YAMNET = 521
NOMES_YAMNET = [f"classe_{i}" for i in range(N_CLASSES_YAMNET)]
for _nome, _indice in INDICES_YAMNET.items():
    NOMES_YAMNET[_indice] = _nome


@dataclass(frozen=True, eq=False)
class CasoInjetado:
    id: str
    rotulo: str
    sinal: np.ndarray
    falas: list[dict]
    respiratorio: tuple[tuple[int, str, float], ...] = ()
    """Eventos respiratórios injetados como scores: ``(quadro, classe do AudioSet, score)``."""


def sessao_sintetica(
    eventos: Sequence[tuple[int, str, float]], quadros: int
) -> Callable[[np.ndarray], np.ndarray]:
    """Sessão do YAMNet que devolve zeros e os scores dados. Testa as regras, não o modelo."""
    scores = np.zeros((max(quadros, 1), N_CLASSES_YAMNET), dtype=np.float32)
    for quadro, classe, valor in eventos:
        if quadro < scores.shape[0]:
            scores[quadro, INDICES_YAMNET[classe]] = valor
    return lambda _waveform: scores


def _frases(rng: np.random.Generator, n: tuple[int, int], duracao: tuple[float, float],
            palavras_por_s: tuple[float, float], pausa: tuple[float, float]) -> list[Frase]:
    frases = []
    for _ in range(int(rng.integers(n[0], n[1] + 1))):
        d = float(rng.uniform(*duracao))
        frases.append(Frase(d, max(1, round(d * float(rng.uniform(*palavras_por_s)))),
                            float(rng.uniform(*pausa))))
    return frases


def gerar_caso(rotulo: str, semente: int) -> CasoInjetado:
    """Um caso do ``rotulo``, com parâmetros sorteados em faixas clínicas plausíveis.

    As faixas descrevem a alteração pretendida (voz soprosa, fala lenta e pausada, jitter e
    shimmer altos...), não foram ajustadas até as regras acertarem.
    """
    rng = np.random.default_rng(semente)
    f0 = float(rng.uniform(110, 220))
    caso_id = f"{rotulo}-{semente}"
    respiratorio: tuple[tuple[int, str, float], ...] = ()

    if rotulo == "normal":
        voz = ParametrosVoz(
            f0_hz=f0, jitter_pct=float(rng.uniform(0.1, 0.6)),
            shimmer_pct=float(rng.uniform(0.8, 2.4)), snr_db=float(rng.uniform(24, 34)),
            variacao_f0_st=float(rng.uniform(3.5, 6.0)))
        frases = _frases(rng, (3, 5), (2.0, 3.0), (3.0, 3.8), (0.3, 0.6))
    elif rotulo == "fadiga_vocal":
        voz = ParametrosVoz(
            f0_hz=f0, jitter_pct=float(rng.uniform(0.2, 0.6)),
            shimmer_pct=float(rng.uniform(1.0, 2.0)), snr_db=float(rng.uniform(26, 34)),
            variacao_f0_st=float(rng.uniform(0.4, 1.2)))
        frases = _frases(rng, (3, 4), (1.5, 2.2), (1.2, 1.6), (1.4, 2.0))
    elif rotulo == "dificuldade_respiratoria":
        voz = ParametrosVoz(
            f0_hz=f0, jitter_pct=float(rng.uniform(0.2, 0.8)),
            shimmer_pct=float(rng.uniform(1.0, 3.0)), snr_db=float(rng.uniform(14, 22)),
            variacao_f0_st=float(rng.uniform(3.0, 5.0)))
        frases = _frases(rng, (3, 4), (1.0, 1.8), (1.5, 2.2), (1.4, 2.2))
    elif rotulo == "possivel_disartria":
        voz = ParametrosVoz(
            f0_hz=f0, jitter_pct=float(rng.uniform(1.5, 3.0)),
            shimmer_pct=float(rng.uniform(6.0, 9.0)), snr_db=float(rng.uniform(11, 17)),
            variacao_f0_st=float(rng.uniform(2.0, 4.0)))
        frases = _frases(rng, (3, 3), (2.2, 3.0), (1.0, 1.6), (0.4, 0.8))
    else:
        raise ValueError(f"rótulo desconhecido: {rotulo!r}")

    sinal, falas = montar_fala(frases, voz, semente=semente)
    if rotulo == "dificuldade_respiratoria":
        duracao_s = sinal.size / TAXA_HZ
        n_quadros = max(int((duracao_s - 1.0) / 0.48), 4)
        escolhidos = rng.choice(n_quadros, size=int(rng.integers(2, 5)), replace=False)
        classes = list(INDICES_YAMNET)
        respiratorio = tuple(
            (int(q), classes[int(rng.integers(len(classes)))], float(rng.uniform(0.45, 0.9)))
            for q in sorted(escolhidos))
    return CasoInjetado(caso_id, rotulo, sinal, falas, respiratorio)


def gerar_casos(por_classe: int, semente: int = 0) -> list[CasoInjetado]:
    """``por_classe`` casos de cada rótulo, reprodutíveis pela ``semente``."""
    return [
        gerar_caso(rotulo, semente * 100_000 + i * 10 + k)
        for k, rotulo in enumerate(ROTULOS)
        for i in range(por_classe)
    ]
