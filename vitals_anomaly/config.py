"""Leitura do ``config.yaml`` da US15.

Todos os parâmetros vêm do arquivo; o código não tem valores padrão escondidos.
Uma chave ausente gera :class:`ConfigError` com o caminho da chave.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .schema import NUMERIC_VITALS, REQUIRED_VITALS

DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.yaml"


class ConfigError(ValueError):
    """Configuração ausente ou inválida."""


@dataclass(frozen=True)
class EICUFiles:
    periodic: str
    aperiodic: str
    patient: str


@dataclass(frozen=True)
class EICUConfig:
    base_url: str
    data_dir: Path
    files: EICUFiles
    anchor_date: date
    patient_id_prefix: str
    fahrenheit_range: tuple[float, float]


@dataclass(frozen=True)
class SyntheticVitalConfig:
    baseline_mean: float
    baseline_sd: float
    circadian_amplitude: float
    ar_phi: float
    ar_sd: float
    noise_sd: float
    min: float
    max: float
    resolution: float


@dataclass(frozen=True)
class SyntheticConfig:
    seed: int
    n_patients: int
    duration_hours: float
    freq_minutes: float
    start: pd.Timestamp
    o2_supplemental_prob: float
    missing_rate: float
    circadian_peak_hour: float
    vitals: dict[str, SyntheticVitalConfig]
    bp_coupling: float


@dataclass(frozen=True)
class VitalsConfig:
    eicu: EICUConfig
    synthetic: SyntheticConfig
    source: Path | None = None


def _get(data: Mapping[str, Any], path: str) -> Any:
    atual: Any = data
    for parte in path.split("."):
        if not isinstance(atual, Mapping) or parte not in atual:
            raise ConfigError(f"chave obrigatória ausente no config: '{path}'")
        atual = atual[parte]
    return atual


def _positivo(valor: Any, path: str) -> float:
    if not isinstance(valor, (int, float)) or isinstance(valor, bool) or valor <= 0:
        raise ConfigError(f"'{path}' deve ser um número positivo (recebido: {valor!r})")
    return valor


def _fracao(valor: Any, path: str) -> float:
    if not isinstance(valor, (int, float)) or not 0.0 <= valor <= 1.0:
        raise ConfigError(f"'{path}' deve estar em [0, 1] (recebido: {valor!r})")
    return float(valor)


def _vital_sintetico(data: Mapping[str, Any], nome: str) -> SyntheticVitalConfig:
    base = f"sources.synthetic.vitals.{nome}"
    g = lambda chave: _get(data, f"{base}.{chave}")
    phi = float(g("ar_phi"))
    if not 0.0 <= phi < 1.0:
        raise ConfigError(f"'{base}.ar_phi' deve estar em [0, 1) para o AR(1) ser estacionário (recebido: {phi})")
    minimo, maximo = float(g("min")), float(g("max"))
    if minimo >= maximo:
        raise ConfigError(f"'{base}': min ({minimo}) deve ser menor que max ({maximo})")
    return SyntheticVitalConfig(
        baseline_mean=float(g("baseline_mean")),
        baseline_sd=float(g("baseline_sd")),
        circadian_amplitude=float(g("circadian_amplitude")),
        ar_phi=phi,
        ar_sd=float(g("ar_sd")),
        noise_sd=float(g("noise_sd")),
        min=minimo,
        max=maximo,
        resolution=float(_positivo(g("resolution"), f"{base}.resolution")),
    )


def config_from_dict(data: Mapping[str, Any], source: Path | None = None) -> VitalsConfig:
    g = lambda path: _get(data, path)

    faixa_f = tuple(float(v) for v in g("sources.eicu.fahrenheit_range"))
    if len(faixa_f) != 2 or faixa_f[0] >= faixa_f[1]:
        raise ConfigError(f"'sources.eicu.fahrenheit_range' deve ser [min, max] crescente (recebido: {faixa_f})")

    nomes_vitais = g("sources.synthetic.vitals")
    desconhecidos = sorted(set(nomes_vitais) - set(NUMERIC_VITALS))
    if desconhecidos:
        raise ConfigError(f"'sources.synthetic.vitals' tem sinais desconhecidos: {desconhecidos}")
    obrigatorios_ausentes = [v for v in REQUIRED_VITALS if v not in nomes_vitais]
    if obrigatorios_ausentes:
        raise ConfigError(f"'sources.synthetic.vitals' precisa dos sinais obrigatórios: {obrigatorios_ausentes}")

    return VitalsConfig(
        eicu=EICUConfig(
            base_url=str(g("sources.eicu.base_url")),
            data_dir=Path(g("sources.eicu.data_dir")),
            files=EICUFiles(
                periodic=str(g("sources.eicu.files.periodic")),
                aperiodic=str(g("sources.eicu.files.aperiodic")),
                patient=str(g("sources.eicu.files.patient")),
            ),
            anchor_date=date.fromisoformat(str(g("sources.eicu.anchor_date"))),
            patient_id_prefix=str(g("sources.eicu.patient_id_prefix")),
            fahrenheit_range=(faixa_f[0], faixa_f[1]),
        ),
        synthetic=SyntheticConfig(
            seed=int(g("sources.synthetic.seed")),
            n_patients=int(_positivo(g("sources.synthetic.n_patients"), "sources.synthetic.n_patients")),
            duration_hours=float(_positivo(g("sources.synthetic.duration_hours"), "sources.synthetic.duration_hours")),
            freq_minutes=float(_positivo(g("sources.synthetic.freq_minutes"), "sources.synthetic.freq_minutes")),
            start=pd.Timestamp(g("sources.synthetic.start")).tz_convert("UTC"),
            o2_supplemental_prob=_fracao(g("sources.synthetic.o2_supplemental_prob"), "sources.synthetic.o2_supplemental_prob"),
            missing_rate=_fracao(g("sources.synthetic.missing_rate"), "sources.synthetic.missing_rate"),
            circadian_peak_hour=float(g("sources.synthetic.circadian_peak_hour")),
            vitals={nome: _vital_sintetico(data, nome) for nome in nomes_vitais},
            bp_coupling=_fracao(g("sources.synthetic.bp_coupling"), "sources.synthetic.bp_coupling"),
        ),
        source=source,
    )


def load_config(path: str | Path | None = None) -> VitalsConfig:
    """Carrega o config; sem ``path``, usa o ``config.yaml`` do pacote."""
    path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, Mapping):
        raise ConfigError(f"{path}: o arquivo deve conter um mapeamento YAML")
    return config_from_dict(data, source=path)
