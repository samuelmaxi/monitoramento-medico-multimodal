"""Leitura do ``icu/chartevents.csv.gz`` do MIMIC-IV Clinical Database Demo (PhysioNet)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

CHARTEVENTS = Path("dados/mimic-iv-demo/chartevents.csv.gz")
URL_BASE = "https://physionet.org/files/mimic-iv-demo/2.2/icu"

ITEMIDS = {
    220045: "fc",
    220179: "pas",
    220050: "pas",  # não invasiva / arterial
    220180: "pad",
    220051: "pad",
    220277: "spo2",
    220210: "fr",
    223762: "temp",
    223761: "temp_f",
    220739: "gcs_olhos",  # 4 = abre espontaneamente
    223834: "o2_fluxo",  # L/min
}


def carregar(caminho: str | Path = CHARTEVENTS) -> pd.DataFrame:
    """Formato longo ``subject_id, instante (UTC), sinal, valor``, ordenado no tempo.

    Não limpa nada: a limpeza de artefatos é do detector, para valer também no streaming.
    """
    df = pd.read_csv(caminho, usecols=["subject_id", "charttime", "itemid", "valuenum"])
    df = df[df.itemid.isin(ITEMIDS)].dropna(subset=["valuenum"])
    df = df.assign(
        instante=pd.to_datetime(df.charttime, utc=True),
        sinal=df.itemid.map(ITEMIDS),
        valor=df.valuenum.astype(float),
    )
    f = df.sinal == "temp_f"
    df.loc[f, "valor"] = (df.loc[f, "valor"] - 32) * 5 / 9
    g = df.sinal == "gcs_olhos"
    # Proxy de ACVPU: só o paciente "A" abre os olhos espontaneamente.
    df.loc[g, "valor"] = (df.loc[g, "valor"] == 4).astype(float)
    o = df.sinal == "o2_fluxo"
    df.loc[o, "valor"] = (df.loc[o, "valor"] > 0).astype(float)
    df["sinal"] = df.sinal.replace(
        {"temp_f": "temp", "gcs_olhos": "alerta", "o2_fluxo": "o2_suplementar"}
    )
    return (
        df[["subject_id", "instante", "sinal", "valor"]]
        .sort_values("instante", kind="stable")
        .reset_index(drop=True)
    )
