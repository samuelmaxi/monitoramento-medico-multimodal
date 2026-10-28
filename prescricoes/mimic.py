"""Leitura do ``hosp/`` do MIMIC-IV Clinical Database Demo 2.2 (PhysioNet, ODbL).

``prescriptions`` tem medicamento, dose, unidade, via, início e fim; a
frequência por extenso (``Q6H:PRN``, ``TITRATE TO``) e a escala móvel vêm da
``pharmacy``, ligada por ``pharmacy_id``. ``admissions`` dá alta e óbito, que
separam suspensão de verdade de fim de internação.

Fica de fora o que não é medicamento do paciente: ``drug_type`` ``BASE``
(diluente da bolsa) e ``ADDITIVE``. Linhas idênticas (mesmo medicamento, dose,
via, início e fim, só ``pharmacy_id`` diferente: 619 no demo) são a mesma
ordem duplicada na extração e viram uma.

O MIMIC desloca as datas para o futuro (2110–2210); elas ficam como estão, em UTC.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path

import pandas as pd

from .modelos import Internacao, Prescricao
from .normalizacao import grupo_de_via, interpretar_dose, tipo_de_frequencia
from .principios import principios_ativos

DIRETORIO = Path("dados/mimic-iv-demo")
URL_BASE = "https://physionet.org/files/mimic-iv-demo/2.2/hosp"
TABELAS = ("prescriptions", "pharmacy", "admissions")

_CHAVE_DUPLICATA = [
    "subject_id", "hadm_id", "drug", "starttime", "stoptime", "dose_val_rx", "dose_unit_rx", "route",
]


def _texto(valor: object) -> str:
    return "" if valor is None or (isinstance(valor, float) and pd.isna(valor)) else str(valor).strip()


def _instante(valor: object) -> datetime | None:
    if valor is None or pd.isna(valor):
        return None
    return pd.Timestamp(valor).tz_localize("UTC").to_pydatetime()


def _numero(valor: object) -> float | None:
    if valor is None or pd.isna(valor):
        return None
    return float(valor)


def baixar(diretorio: str | Path = DIRETORIO) -> list[Path]:
    """Baixa as três tabelas (acesso aberto, sem credencial)."""
    import urllib.request

    diretorio = Path(diretorio)
    diretorio.mkdir(parents=True, exist_ok=True)
    caminhos = []
    for tabela in TABELAS:
        destino = diretorio / f"{tabela}.csv.gz"
        if not destino.exists():
            urllib.request.urlretrieve(f"{URL_BASE}/{tabela}.csv.gz", destino)
        caminhos.append(destino)
    return caminhos


def prescricoes_de_dataframe(df: pd.DataFrame) -> list[Prescricao]:
    """Linhas no formato ``prescriptions`` (+ colunas da ``pharmacy``) → :class:`Prescricao`."""
    prescricoes = []
    for linha in df.itertuples(index=False):
        inicio = _instante(linha.starttime)
        if inicio is None:
            continue
        fim = _instante(linha.stoptime)
        if fim is not None and fim < inicio:
            # 607 linhas do demo terminam antes de começar (quase sempre 1 h
            # antes, como insulina): ordem cancelada antes da primeira dose.
            fim = inicio
        escala = _texto(getattr(linha, "sliding_scale", "")).upper() == "Y"
        frequencia = _texto(getattr(linha, "frequency", "")) or None
        prescricoes.append(
            Prescricao(
                ordem_id=str(linha.pharmacy_id),
                paciente=str(linha.subject_id),
                internacao=None if pd.isna(linha.hadm_id) else str(int(linha.hadm_id)),
                medicamento=_texto(linha.drug),
                principios=principios_ativos(_texto(linha.drug)),
                dose=interpretar_dose(linha.dose_val_rx, linha.dose_unit_rx),
                via=_texto(linha.route),
                via_grupo=grupo_de_via(linha.route),
                frequencia=frequencia,
                tipo_frequencia=tipo_de_frequencia(frequencia, escala_movel=escala),
                doses_24h=_numero(linha.doses_per_24_hrs) or None,
                inicio=inicio,
                fim=fim,
                dose_texto=_texto(linha.dose_val_rx),
                unidade_texto=_texto(linha.dose_unit_rx),
                apresentacao=_texto(linha.prod_strength),
                status=_texto(getattr(linha, "status", "")) or None,
            )
        )
    prescricoes.sort(key=lambda p: (p.paciente, p.inicio, p.ordem_id))
    return prescricoes


def carregar_prescricoes(
    diretorio: str | Path = DIRETORIO, *, pacientes: Iterable[str] | None = None
) -> list[Prescricao]:
    """Prescrições de medicamento, ordenadas por paciente e início."""
    diretorio = Path(diretorio)
    df = pd.read_csv(
        diretorio / "prescriptions.csv.gz", low_memory=False, parse_dates=["starttime", "stoptime"]
    )
    df = df[df.drug_type == "MAIN"]
    if pacientes is not None:
        df = df[df.subject_id.astype(str).isin(set(pacientes))]
    df = df.drop_duplicates(_CHAVE_DUPLICATA)
    farmacia = diretorio / "pharmacy.csv.gz"
    if farmacia.exists():
        ph = pd.read_csv(
            farmacia, low_memory=False, usecols=["pharmacy_id", "frequency", "sliding_scale", "status"]
        )
        df = df.merge(ph.drop_duplicates("pharmacy_id"), on="pharmacy_id", how="left")
    return prescricoes_de_dataframe(df)


def carregar_internacoes(diretorio: str | Path = DIRETORIO) -> dict[str, Internacao]:
    df = pd.read_csv(
        Path(diretorio) / "admissions.csv.gz", parse_dates=["admittime", "dischtime", "deathtime"]
    )
    return {
        str(l.hadm_id): Internacao(
            id=str(l.hadm_id),
            paciente=str(l.subject_id),
            admissao=_instante(l.admittime),
            alta=_instante(l.dischtime),
            obito=_instante(l.deathtime),
        )
        for l in df.itertuples(index=False)
    }


def por_internacao(prescricoes: Iterable[Prescricao]) -> Mapping[tuple[str, str | None], list[Prescricao]]:
    grupos: dict[tuple[str, str | None], list[Prescricao]] = {}
    for p in prescricoes:
        grupos.setdefault((p.paciente, p.internacao), []).append(p)
    return grupos
