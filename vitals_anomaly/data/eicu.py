"""Adapter do eICU Collaborative Research Database Demo (PhysioNet) para o schema canônico.

Tabelas usadas (CSV gzip baixados por ``scripts/download_data.py``):

* ``vitalPeriodic``: medianas de 5 min do monitor (FC, SpO2, FR, temperatura e
  pressão arterial invasiva);
* ``vitalAperiodic``: medidas esporádicas, das quais só a pressão não invasiva
  (manguito) é usada;
* ``patient``: hora do dia da admissão na unidade, usada para montar o timestamp.

O ``patient_id`` canônico é o ``patientunitstayid`` (uma internação em UTI),
porque é a ele que as séries do eICU estão ligadas.

Nada é interpolado nem preenchido: as duas tabelas são unidas pelos instantes
em que existem medidas, e só valores do mesmo instante são combinados.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import EICUConfig
from ..schema import BPSource, coerce_vitals, validate_vitals

logger = logging.getLogger(__name__)

STAY_COL = "patientunitstayid"
OFFSET_COL = "observationoffset"

# Coluna do eICU -> coluna canônica. Todas já estão nas unidades canônicas
# (bpm, %, irpm, mmHg), exceto a temperatura, que às vezes vem em °F.
PERIODIC_MAP: dict[str, str] = {
    "heartrate": "hr",
    "sao2": "spo2",
    "respiration": "rr",
    "temperature": "temp",
    "systemicsystolic": "sbp_inv",
    "systemicdiastolic": "dbp_inv",
}
APERIODIC_MAP: dict[str, str] = {
    "noninvasivesystolic": "sbp_ni",
    "noninvasivediastolic": "dbp_ni",
}
PATIENT_COLS: tuple[str, ...] = (STAY_COL, "unitadmittime24")


class EICUDataError(FileNotFoundError):
    """Arquivos do eICU ausentes ou com colunas inesperadas."""


def _ler(caminho: Path, colunas: Iterable[str]) -> pd.DataFrame:
    if not caminho.exists():
        raise EICUDataError(f"{caminho} não encontrado. Rode: python scripts/download_data.py")
    colunas = list(colunas)
    try:
        return pd.read_csv(caminho, usecols=colunas)
    except ValueError as exc:  # usecols com coluna inexistente
        raise EICUDataError(f"{caminho}: colunas esperadas {colunas} ({exc})") from exc


def read_eicu_tables(config: EICUConfig, data_dir: Path | None = None) -> dict[str, pd.DataFrame]:
    """Lê só as colunas necessárias das três tabelas."""
    pasta = Path(data_dir) if data_dir is not None else config.data_dir
    return {
        "periodic": _ler(pasta / config.files.periodic, [STAY_COL, OFFSET_COL, *PERIODIC_MAP]),
        "aperiodic": _ler(pasta / config.files.aperiodic, [STAY_COL, OFFSET_COL, *APERIODIC_MAP]),
        "patient": _ler(pasta / config.files.patient, PATIENT_COLS),
    }


def fahrenheit_to_celsius(temp: pd.Series, fahrenheit_range: tuple[float, float]) -> pd.Series:
    """Converte para °C os valores que caem na faixa plausível em °F.

    Uma temperatura corporal entre 86 e 113 é impossível em °C e típica em °F
    (30-45 °C), então a decisão é feita valor a valor. O resto não é alterado.
    """
    lo, hi = fahrenheit_range
    em_f = temp.between(lo, hi)
    return temp.where(~em_f, (temp - 32.0) * 5.0 / 9.0)


def _agrupar_por_instante(df: pd.DataFrame) -> pd.DataFrame:
    """Média das linhas repetidas no mesmo (internação, offset).

    Combina apenas medidas do mesmo instante, então não usa dados futuros.
    """
    return df.groupby([STAY_COL, OFFSET_COL], sort=False).mean()


def _escolher_pressao(df: pd.DataFrame) -> pd.DataFrame:
    """Prefere a pressão invasiva; usa o manguito só quando não houver invasiva.

    A escolha é pelo par sistólica/diastólica, para nunca misturar a sistólica
    de uma fonte com a diastólica de outra.
    """
    tem_inv = df[["sbp_inv", "dbp_inv"]].notna().any(axis=1)
    tem_ni = df[["sbp_ni", "dbp_ni"]].notna().any(axis=1)
    df["sbp"] = np.where(tem_inv, df["sbp_inv"], df["sbp_ni"])
    df["dbp"] = np.where(tem_inv, df["dbp_inv"], df["dbp_ni"])
    df["bp_source"] = np.select(
        [tem_inv, tem_ni], [BPSource.INVASIVE.value, BPSource.NONINVASIVE.value], default=None
    )
    return df.drop(columns=["sbp_inv", "dbp_inv", "sbp_ni", "dbp_ni"])


def _selecionar_internacoes(
    disponiveis: np.ndarray,
    patient_ids: Iterable[int | str] | None,
    max_patients: int | None,
    prefixo: str,
) -> np.ndarray:
    escolhidas = np.sort(disponiveis)
    if patient_ids is not None:
        ids = [int(str(p).removeprefix(prefixo)) for p in patient_ids]
        escolhidas = escolhidas[np.isin(escolhidas, ids)]
    if max_patients is not None:
        escolhidas = escolhidas[:max_patients]
    return escolhidas


def eicu_to_canonical(
    periodic: pd.DataFrame,
    aperiodic: pd.DataFrame,
    patient: pd.DataFrame,
    config: EICUConfig,
    *,
    patient_ids: Iterable[int | str] | None = None,
    max_patients: int | None = None,
) -> pd.DataFrame:
    """Converte as tabelas cruas do eICU para o DataFrame canônico.

    Args:
        periodic, aperiodic, patient: tabelas como lidas por :func:`read_eicu_tables`.
        config: seção ``sources.eicu`` do config.
        patient_ids: restringe a estas internações (``patientunitstayid`` ou id canônico).
        max_patients: mantém só as N primeiras internações (por id), após ``patient_ids``.
    """
    periodic = periodic[[STAY_COL, OFFSET_COL, *PERIODIC_MAP]]
    aperiodic = aperiodic[[STAY_COL, OFFSET_COL, *APERIODIC_MAP]]
    disponiveis = np.union1d(periodic[STAY_COL].unique(), aperiodic[STAY_COL].unique())
    estadias = _selecionar_internacoes(disponiveis, patient_ids, max_patients, config.patient_id_prefix)
    periodic = periodic[periodic[STAY_COL].isin(estadias)]
    aperiodic = aperiodic[aperiodic[STAY_COL].isin(estadias)]

    p = _agrupar_por_instante(periodic.rename(columns=PERIODIC_MAP))
    a = _agrupar_por_instante(aperiodic.rename(columns=APERIODIC_MAP).dropna(subset=["sbp_ni", "dbp_ni"], how="all"))
    df = _escolher_pressao(p.join(a, how="outer")).reset_index()

    df["temp"] = fahrenheit_to_celsius(df["temp"], config.fahrenheit_range)
    df = df.dropna(subset=["hr", "sbp", "dbp", "spo2", "rr", "temp"], how="all")

    admissao = patient.drop_duplicates(STAY_COL).set_index(STAY_COL)["unitadmittime24"]
    df["admit_time"] = pd.to_timedelta(df[STAY_COL].map(admissao))
    sem_admissao = df["admit_time"].isna()
    if sem_admissao.any():
        logger.warning(
            "%d linhas de %d internações sem hora de admissão na tabela patient foram descartadas",
            int(sem_admissao.sum()),
            df.loc[sem_admissao, STAY_COL].nunique(),
        )
        df = df[~sem_admissao]

    ancora = pd.Timestamp(config.anchor_date, tz="UTC")
    df["timestamp"] = ancora + df["admit_time"] + pd.to_timedelta(df[OFFSET_COL], unit="min")
    df["patient_id"] = config.patient_id_prefix + df[STAY_COL].astype(str)

    return validate_vitals(coerce_vitals(df.drop(columns=[STAY_COL, OFFSET_COL, "admit_time"])))


def load_eicu_vitals(
    config: EICUConfig,
    *,
    data_dir: Path | None = None,
    patient_ids: Iterable[int | str] | None = None,
    max_patients: int | None = None,
) -> pd.DataFrame:
    """Lê os arquivos do eICU Demo e devolve o DataFrame canônico."""
    tabelas = read_eicu_tables(config, data_dir)
    return eicu_to_canonical(
        tabelas["periodic"],
        tabelas["aperiodic"],
        tabelas["patient"],
        config,
        patient_ids=patient_ids,
        max_patients=max_patients,
    )
