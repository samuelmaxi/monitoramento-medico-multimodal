"""Ponto de entrada único para carregar sinais vitais no schema canônico."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import pandas as pd

from ..config import VitalsConfig, load_config
from .eicu import load_eicu_vitals
from .synthetic import generate_synthetic_vitals

Source = Literal["eicu", "synthetic"]
SOURCES: tuple[str, ...] = ("eicu", "synthetic")


def load_vitals(
    source: Source = "synthetic",
    *,
    config: VitalsConfig | str | Path | None = None,
    patient_ids: Iterable[int | str] | None = None,
    max_patients: int | None = None,
    data_dir: Path | None = None,
    seed: int | None = None,
    duration_hours: float | None = None,
) -> pd.DataFrame:
    """Carrega séries de sinais vitais no schema canônico.

    O resultado sempre passa por :func:`vitals_anomaly.schema.validate_vitals`:
    colunas e dtypes canônicos, ordenado por ``patient_id`` e ``timestamp``, sem
    duplicatas. Unidades: bpm, mmHg, %, irpm e °C (ver ``schema``).

    Args:
        source: ``"eicu"`` (eICU-CRD Demo baixado) ou ``"synthetic"`` (gerador offline).
        config: objeto de config, caminho de um YAML ou ``None`` para o padrão do pacote.
        patient_ids: só eICU — internações a carregar (``patientunitstayid`` ou id canônico).
        max_patients: no eICU, as N primeiras internações; no sintético, quantos gerar.
        data_dir: só eICU — sobrescreve ``sources.eicu.data_dir``.
        seed, duration_hours: só sintético — sobrescrevem o config.
    """
    cfg = config if isinstance(config, VitalsConfig) else load_config(config)

    if source == "eicu":
        if seed is not None or duration_hours is not None:
            raise ValueError("seed e duration_hours só valem para source='synthetic'")
        return load_eicu_vitals(cfg.eicu, data_dir=data_dir, patient_ids=patient_ids, max_patients=max_patients)
    if source == "synthetic":
        if patient_ids is not None or data_dir is not None:
            raise ValueError("patient_ids e data_dir só valem para source='eicu'")
        return generate_synthetic_vitals(
            cfg.synthetic, n_patients=max_patients, duration_hours=duration_hours, seed=seed
        )
    raise ValueError(f"source desconhecida: {source!r} (opções: {', '.join(SOURCES)})")
