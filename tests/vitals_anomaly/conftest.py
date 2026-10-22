import math

import pandas as pd
import pytest

from vitals_anomaly.config import load_config

NAN = math.nan


@pytest.fixture(scope="session")
def vitals_config():
    return load_config()


@pytest.fixture
def eicu_tables():
    """Amostra mínima das tabelas do eICU cobrindo os casos de borda do adapter.

    Internação 100: offset negativo, offset repetido, temperatura em °F,
    pressão invasiva e não invasiva no mesmo instante, linha só com PVC.
    Internação 200: um instante só. 300: só pressão não invasiva.
    400: sem linha na tabela patient. Linhas fora de ordem de propósito.
    """
    periodic = pd.DataFrame(
        [
            # id, stay, offset, temperature, sao2, heartrate, respiration, cvp, sys, dia
            (7, 400, 0, NAN, 99, 70, 14, NAN, NAN, NAN),
            (1, 100, 10, NAN, 97, 80, 16, NAN, 120, 70),
            (6, 200, 0, NAN, 99, 70, 14, NAN, NAN, NAN),
            (2, 100, 5, 98.6, 96, 82, 18, NAN, NAN, NAN),
            (3, 100, -5, 37.2, 95, 90, NAN, NAN, NAN, NAN),
            (4, 100, 5, NAN, NAN, 84, NAN, NAN, NAN, NAN),
            (5, 100, 15, NAN, NAN, NAN, NAN, 8, NAN, NAN),
        ],
        columns=[
            "vitalperiodicid", "patientunitstayid", "observationoffset", "temperature", "sao2",
            "heartrate", "respiration", "cvp", "systemicsystolic", "systemicdiastolic",
        ],
    )
    aperiodic = pd.DataFrame(
        [
            (1, 100, 10, 130, 80, 97),
            (2, 100, 20, 125, 75, 92),
            (3, 300, 30, 110, 60, 77),
            (4, 200, 0, NAN, NAN, 50),
        ],
        columns=[
            "vitalaperiodicid", "patientunitstayid", "observationoffset",
            "noninvasivesystolic", "noninvasivediastolic", "noninvasivemean",
        ],
    )
    patient = pd.DataFrame(
        [(100, "002-1", "22:30:00"), (200, "002-2", "08:00:00"), (300, "002-3", "00:00:00")],
        columns=["patientunitstayid", "uniquepid", "unitadmittime24"],
    )
    return {"periodic": periodic, "aperiodic": aperiodic, "patient": patient}


@pytest.fixture
def eicu_dir(tmp_path, eicu_tables, vitals_config):
    """Grava a amostra como os CSV gzip do PhysioNet, com os nomes do config."""
    files = vitals_config.eicu.files
    for chave, nome in (("periodic", files.periodic), ("aperiodic", files.aperiodic), ("patient", files.patient)):
        eicu_tables[chave].to_csv(tmp_path / nome, index=False, compression="gzip")
    return tmp_path
