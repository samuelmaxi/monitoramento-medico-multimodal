"""Gerador sintético de sinais vitais (fallback offline e base dos testes).

Cada sinal de cada paciente é a soma de:

* **baseline individual** sorteado uma vez por paciente (ex.: FC de repouso);
* **componente circadiano** ``A·cos(2π(h − pico)/24)``, com ``h`` a hora do dia;
* **ruído fisiológico** AR(1) (variações lentas e autocorrelacionadas);
* **ruído de medição** branco;

seguida de corte nos limites físicos e arredondamento à resolução do monitor.
A sistólica e a diastólica compartilham parte do baseline e do ruído AR(1)
(``bp_coupling``) e a diastólica é mantida abaixo da sistólica.

Não há anomalias injetadas aqui: a série é "normal" por construção.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import SyntheticConfig, SyntheticVitalConfig
from ..schema import coerce_vitals, validate_vitals

PATIENT_ID_PREFIX = "syn_"
# Menor diferença sistólica − diastólica aceita, em mmHg.
_MIN_PULSE_PRESSURE = 10.0


def _ar1(rng: np.random.Generator, n: int, phi: float, sd: float) -> np.ndarray:
    """Série AR(1) estacionária: e[t] = phi·e[t−1] + N(0, sd)."""
    choques = rng.normal(0.0, sd, n)
    serie = np.empty(n)
    serie[0] = rng.normal(0.0, sd / np.sqrt(1.0 - phi**2)) if n else 0.0
    for t in range(1, n):
        serie[t] = phi * serie[t - 1] + choques[t]
    return serie


def _circadiano(timestamps: pd.DatetimeIndex, amplitude: float, pico_h: float) -> np.ndarray:
    hora = timestamps.hour + timestamps.minute / 60.0 + timestamps.second / 3600.0
    return amplitude * np.cos(2.0 * np.pi * (np.asarray(hora) - pico_h) / 24.0)


def _finalizar(valores: np.ndarray, cfg: SyntheticVitalConfig) -> np.ndarray:
    valores = np.clip(valores, cfg.min, cfg.max)
    # O segundo round tira o resíduo de ponto flutuante (36.800000000000004).
    return np.round(np.round(valores / cfg.resolution) * cfg.resolution, 6)


def _paciente(
    rng: np.random.Generator,
    patient_id: str,
    timestamps: pd.DatetimeIndex,
    config: SyntheticConfig,
) -> pd.DataFrame:
    n = len(timestamps)
    z_baseline: dict[str, float] = {}
    sinais: dict[str, np.ndarray] = {}
    ruido_ar: dict[str, np.ndarray] = {}

    for nome, cfg in config.vitals.items():
        z_baseline[nome] = rng.standard_normal()
        ruido_ar[nome] = _ar1(rng, n, cfg.ar_phi, cfg.ar_sd)
        sinais[nome] = (
            _circadiano(timestamps, cfg.circadian_amplitude, config.circadian_peak_hour)
            + rng.normal(0.0, cfg.noise_sd, n)
        )

    # A diastólica herda parte do baseline e do AR(1) da sistólica (correlação
    # entre pacientes e ao longo do tempo), na escala dela.
    c = config.bp_coupling
    z_baseline["dbp"] = c * z_baseline["sbp"] + np.sqrt(1.0 - c**2) * z_baseline["dbp"]
    escala = config.vitals["dbp"].ar_sd / config.vitals["sbp"].ar_sd if config.vitals["sbp"].ar_sd else 0.0
    ruido_ar["dbp"] = c * escala * ruido_ar["sbp"] + np.sqrt(1.0 - c**2) * ruido_ar["dbp"]

    for nome, cfg in config.vitals.items():
        baseline = cfg.baseline_mean + cfg.baseline_sd * z_baseline[nome]
        sinais[nome] = _finalizar(baseline + sinais[nome] + ruido_ar[nome], cfg)
    sinais["dbp"] = np.minimum(sinais["dbp"], sinais["sbp"] - _MIN_PULSE_PRESSURE)

    if config.missing_rate > 0:
        sinais = {
            nome: np.where(rng.random(n) < config.missing_rate, np.nan, valores) for nome, valores in sinais.items()
        }

    return pd.DataFrame(
        {
            "patient_id": patient_id,
            "timestamp": timestamps,
            **sinais,
            "o2_supplemental": bool(rng.random() < config.o2_supplemental_prob),
            "consciousness": "A",
        }
    )


def generate_synthetic_vitals(
    config: SyntheticConfig,
    *,
    n_patients: int | None = None,
    duration_hours: float | None = None,
    seed: int | None = None,
) -> pd.DataFrame:
    """Gera pacientes sintéticos no schema canônico.

    Os argumentos opcionais sobrescrevem o config (úteis em testes). Cada
    paciente usa um gerador derivado de ``seed`` pelo seu índice, então o
    paciente *i* é idêntico qualquer que seja ``n_patients``.
    """
    n_patients = config.n_patients if n_patients is None else n_patients
    duration_hours = config.duration_hours if duration_hours is None else duration_hours
    seed = config.seed if seed is None else seed
    if n_patients <= 0 or duration_hours <= 0:
        raise ValueError("n_patients e duration_hours devem ser positivos")

    passo = pd.Timedelta(minutes=config.freq_minutes)
    n_amostras = int(pd.Timedelta(hours=duration_hours) / passo)
    timestamps = pd.date_range(config.start, periods=n_amostras, freq=passo)

    filhos = np.random.SeedSequence(seed).spawn(n_patients)
    largura = max(4, len(str(n_patients)))
    frames = [
        _paciente(np.random.default_rng(ss), f"{PATIENT_ID_PREFIX}{i:0{largura}d}", timestamps, config)
        for i, ss in enumerate(filhos, start=1)
    ]
    return validate_vitals(coerce_vitals(pd.concat(frames, ignore_index=True)))
