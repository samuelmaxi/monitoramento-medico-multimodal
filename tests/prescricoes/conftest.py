"""Fixtures da US16: ordens sintéticas no formato do MIMIC já normalizado."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from prescricoes.config import carregar_config
from prescricoes.modelos import Internacao, Prescricao
from prescricoes.normalizacao import grupo_de_via, interpretar_dose, tipo_de_frequencia
from prescricoes.principios import principios_ativos

CHAVE_TESTE = "chave-de-teste-com-mais-de-32-bytes-000000"
T0 = datetime(2150, 1, 1, 8, tzinfo=timezone.utc)
H = timedelta(hours=1)


@pytest.fixture(autouse=True)
def _chave(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSEUDONYM_KEY", CHAVE_TESTE)


@pytest.fixture(scope="session")
def cfg():
    return carregar_config()


@pytest.fixture
def internacao() -> Internacao:
    return Internacao(id="20000001", paciente="10000001", admissao=T0, alta=T0 + 20 * 24 * H)


_contador = iter(range(10**6))


def ordem(
    nome: str,
    dose: str,
    unidade: str,
    *,
    de: float,
    ate: float | None,
    via: str = "PO",
    freq: str = "DAILY",
    n24: float | None = 1,
    status: str = "Discontinued",
    apresentacao: str = "",
    escala_movel: bool = False,
) -> Prescricao:
    """Ordem com início/fim em horas desde ``T0``."""
    return Prescricao(
        ordem_id=f"o{next(_contador)}",
        paciente="10000001",
        internacao="20000001",
        medicamento=nome,
        principios=principios_ativos(nome),
        dose=interpretar_dose(dose, unidade),
        via=via,
        via_grupo=grupo_de_via(via),
        frequencia=freq,
        tipo_frequencia=tipo_de_frequencia(freq, escala_movel=escala_movel),
        doses_24h=n24,
        inicio=T0 + de * H,
        fim=None if ate is None else T0 + ate * H,
        dose_texto=dose,
        unidade_texto=unidade,
        apresentacao=apresentacao,
        status=status,
    )


@pytest.fixture
def mimic(tmp_path: Path) -> Path:
    """Tabelas no formato do MIMIC: paciente 1 com 3 anomalias, paciente 2 sem nenhuma."""
    linhas = [
        # subject, hadm, pharmacy_id, start, stop, drug, dose, unit, n24, route, prod_strength
        (1, 11, 101, "2150-01-01 08:00", "2150-01-02 08:00", "Metoprolol Tartrate", "12.5", "mg", 2, "PO", "25mg Tab"),
        (1, 11, 102, "2150-01-02 08:00", "2150-01-05 08:00", "Metoprolol Tartrate", "50", "mg", 2, "PO", "25mg Tab"),
        (1, 11, 102, "2150-01-02 08:00", "2150-01-05 08:00", "Metoprolol Tartrate", "50", "mg", 2, "PO", "25mg Tab"),
        (1, 11, 103, "2150-01-01 09:00", "2150-01-03 09:00", "Heparin", "5000", "UNIT", 3, "SC", "5000 Units / mL- 1mL Vial"),
        (2, 22, 201, "2150-02-01 08:00", "2150-02-03 08:00", "Pantoprazole", "40", "mg", 1, "PO", "40mg Tab"),
    ]
    colunas = ["subject_id", "hadm_id", "pharmacy_id", "starttime", "stoptime", "drug", "dose_val_rx",
               "dose_unit_rx", "doses_per_24_hrs", "route", "prod_strength"]
    df = pd.DataFrame(linhas, columns=colunas).assign(drug_type="MAIN")
    df.to_csv(tmp_path / "prescriptions.csv.gz", index=False)
    pd.DataFrame(
        {"pharmacy_id": [101, 102, 103, 201], "frequency": ["BID", "BID", "Q8H", "DAILY"],
         "sliding_scale": ["N"] * 4, "status": ["Inactive (Due to a change order)", "Discontinued",
                                                "Discontinued", "Discontinued via patient discharge"]}
    ).to_csv(tmp_path / "pharmacy.csv.gz", index=False)
    pd.DataFrame(
        {"subject_id": [1, 2], "hadm_id": [11, 22], "admittime": ["2150-01-01 07:00", "2150-02-01 07:00"],
         "dischtime": ["2150-01-10 12:00", "2150-02-03 08:00"], "deathtime": [None, None]}
    ).to_csv(tmp_path / "admissions.csv.gz", index=False)
    return tmp_path
