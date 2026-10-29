"""Audita a ingestão do eICU

    python scripts/auditar_eicu.py
    python scripts/auditar_eicu.py --examples 30

Carrega todas as internações com ``load_vitals("eicu")`` e compara a saída
com os CSV crus, lidos aqui diretamente. Cada verificação imprime [OK] ou
[FALHA] com os números; o script termina com código 1 se alguma falhar.

  A. cobertura de internações
  B. contagem de medidas por sinal e por internação
  C. igualdade valor a valor de todas as linhas
  D. temperatura: totais, conversão °F → °C e uma série real com temperatura
  E. schema canônico

O oráculo (C) é uma reconstrução independente e direta das regras documentadas
em docs/US15_decisoes.md; só a regra de °F → °C é importada do adapter.

Pré-requisito: python scripts/download_data.py
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from vitals_anomaly import load_vitals
from vitals_anomaly.config import load_config
from vitals_anomaly.data.eicu import (
    OFFSET_COL,
    STAY_COL,
    EICUDataError,
    fahrenheit_to_celsius,
)
from vitals_anomaly.schema import SchemaError, validate_vitals

SINAIS = ["hr", "sbp", "dbp", "spo2", "rr", "temp"]
CRU_PERIODIC = {
    "heartrate": "hr",
    "sao2": "spo2",
    "respiration": "rr",
    "temperature": "temp",
    "systemicsystolic": "sbp_inv",
    "systemicdiastolic": "dbp_inv",
}
CRU_APERIODIC = {"noninvasivesystolic": "sbp_ni", "noninvasivediastolic": "dbp_ni"}
SERIE_COM_TEMPERATURA = 1034813
TOLERANCIA = 1e-9


class Auditoria:
    def __init__(self) -> None:
        self.falhas: list[str] = []

    def check(self, ok: bool, texto: str) -> None:
        print(f"  [{'OK' if ok else 'FALHA'}] {texto}")
        if not ok:
            self.falhas.append(texto)


def _titulo(texto: str) -> None:
    print(f"\n{'=' * 80}\n{texto}\n{'=' * 80}")


def _ler_cru(pasta: Path, cfg) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    periodic = pd.read_csv(
        pasta / cfg.files.periodic, usecols=[STAY_COL, OFFSET_COL, *CRU_PERIODIC]
    )
    aperiodic = pd.read_csv(
        pasta / cfg.files.aperiodic, usecols=[STAY_COL, OFFSET_COL, *CRU_APERIODIC]
    )
    patient = pd.read_csv(
        pasta / cfg.files.patient, usecols=[STAY_COL, "unitadmittime24"]
    )
    return (
        periodic.rename(columns=CRU_PERIODIC),
        aperiodic.rename(columns=CRU_APERIODIC),
        patient,
    )


def _esperado(
    periodic: pd.DataFrame, aperiodic: pd.DataFrame, patient: pd.DataFrame, cfg
) -> pd.DataFrame:
    """Reconstrói, a partir do cru, a saída que as regras documentadas exigem."""
    chave = [STAY_COL, OFFSET_COL]
    p = periodic.groupby(chave, as_index=False).mean()
    a = (
        aperiodic.dropna(subset=["sbp_ni", "dbp_ni"], how="all")
        .groupby(chave, as_index=False)
        .mean()
    )
    m = p.merge(a, on=chave, how="outer")

    m["temp"] = fahrenheit_to_celsius(m["temp"], cfg.fahrenheit_range)
    usa_invasiva = m["sbp_inv"].notna() | m["dbp_inv"].notna()
    usa_manguito = ~usa_invasiva & (m["sbp_ni"].notna() | m["dbp_ni"].notna())
    m["sbp"] = m["sbp_inv"].where(usa_invasiva, m["sbp_ni"])
    m["dbp"] = m["dbp_inv"].where(usa_invasiva, m["dbp_ni"])
    m["bp_source"] = np.where(
        usa_invasiva, "invasive", np.where(usa_manguito, "noninvasive", None)
    )
    m = m.dropna(subset=SINAIS, how="all")

    m = m.merge(patient.drop_duplicates(STAY_COL), on=STAY_COL, how="inner")
    m["timestamp"] = (
        pd.Timestamp(cfg.anchor_date, tz="UTC")
        + pd.to_timedelta(m["unitadmittime24"])
        + pd.to_timedelta(m[OFFSET_COL], unit="min")
    )
    m["patient_id"] = cfg.patient_id_prefix + m[STAY_COL].astype(str)
    return m[["patient_id", "timestamp", STAY_COL, OFFSET_COL, *SINAIS, "bp_source"]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="pasta dos CSV (padrão: a do config)",
    )
    parser.add_argument(
        "--examples",
        type=int,
        default=15,
        help="linhas mostradas por exemplo (padrão: 15)",
    )
    args = parser.parse_args(argv)

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)
    cfg = load_config().eicu
    pasta = args.data_dir or cfg.data_dir
    aud = Auditoria()
    inicio = time.perf_counter()

    try:
        df = load_vitals("eicu", data_dir=pasta)
    except EICUDataError as exc:
        print(exc, file=sys.stderr)
        return 1
    periodic, aperiodic, patient = _ler_cru(pasta, cfg)
    esperado = _esperado(periodic, aperiodic, patient, cfg)
    print(
        f"Saída de load_vitals('eicu'): {df['patient_id'].nunique()} internações, {len(df)} linhas"
    )
    print(
        f"Cru: {len(periodic)} linhas vitalPeriodic, {len(aperiodic)} vitalAperiodic, {len(patient)} patient"
    )

    _titulo("A. Cobertura de internações")
    todas = set(patient[STAY_COL])
    com_sinal = set(esperado[STAY_COL])
    saida = {
        int(p.removeprefix(cfg.patient_id_prefix)) for p in df["patient_id"].unique()
    }
    sem_patient = (set(periodic[STAY_COL]) | set(aperiodic[STAY_COL])) - todas
    aud.check(
        saida == com_sinal,
        f"internações na saída ({len(saida)}) == internações do cru com algum sinal ({len(com_sinal)})",
    )
    print(
        f"  [INFO] {len(todas - com_sinal)} internações da tabela patient sem nenhum sinal canônico no cru (ficam de fora)"
    )
    print(
        f"  [INFO] {len(sem_patient)} internações com sinais mas sem linha em patient (descartadas)"
    )

    _titulo("B. Contagem de medidas por sinal, por internação (todas as internações)")
    chave = [STAY_COL, OFFSET_COL]
    cont_saida = df.groupby("patient_id")[SINAIS].count()
    cont_saida.index = cont_saida.index.str.removeprefix(cfg.patient_id_prefix).astype(
        int
    )
    for s in ["hr", "spo2", "rr", "temp"]:
        cru = (
            periodic.dropna(subset=[s]).drop_duplicates(chave).groupby(STAY_COL).size()
        )
        cru = cru.reindex(cont_saida.index, fill_value=0)
        diverge = int((cru != cont_saida[s]).sum())
        aud.check(
            diverge == 0,
            f"{s:5s}: {int(cru.sum()):>9,} medidas no cru = {int(cont_saida[s].sum()):>9,} na saída; internações divergentes: {diverge}",
        )
    pa_inv = periodic.dropna(subset=["sbp_inv", "dbp_inv"], how="all")[chave]
    pa_ni = aperiodic.dropna(subset=["sbp_ni", "dbp_ni"], how="all")[chave]
    pa_cru = pd.concat([pa_inv, pa_ni]).drop_duplicates().groupby(STAY_COL).size()
    pa_saida = df.dropna(subset=["bp_source"]).groupby("patient_id").size()
    pa_saida.index = pa_saida.index.str.removeprefix(cfg.patient_id_prefix).astype(int)
    pa_cru, pa_saida = pa_cru.align(pa_saida, fill_value=0)
    aud.check(
        bool((pa_cru == pa_saida).all()),
        f"PA   : {int(pa_cru.sum()):>9,} instantes com PA no cru (invasiva ou manguito) = {int(pa_saida.sum()):>9,} na saída",
    )

    _titulo("C. Igualdade valor a valor (todas as linhas)")
    junto = esperado.merge(
        df,
        on=["patient_id", "timestamp"],
        how="outer",
        suffixes=("_esp", ""),
        indicator=True,
    )
    so_esperado = int((junto["_merge"] == "left_only").sum())
    so_saida = int((junto["_merge"] == "right_only").sum())
    aud.check(
        so_esperado == 0 and so_saida == 0,
        f"linhas: só no esperado = {so_esperado}, só na saída = {so_saida}",
    )
    ambos = junto[junto["_merge"] == "both"]
    for s in SINAIS:
        a, b = ambos[f"{s}_esp"], ambos[s]
        nulos_diferentes = int((a.isna() != b.isna()).sum())
        dif = (a - b).abs().max()
        dif = 0.0 if pd.isna(dif) else float(dif)
        aud.check(
            nulos_diferentes == 0 and dif <= TOLERANCIA,
            f"{s:5s}: diferença máxima {dif:.1e}; NaN em só um lado: {nulos_diferentes}",
        )
    origem_diferente = int(
        (
            ambos["bp_source_esp"].astype(object).fillna("-")
            != ambos["bp_source"].astype(object).fillna("-")
        ).sum()
    )
    aud.check(
        origem_diferente == 0,
        f"bp_source: linhas com origem diferente: {origem_diferente}",
    )

    _titulo("D. Temperatura")
    temp_cru = periodic.dropna(subset=["temp"])
    stays_temp_cru = temp_cru[STAY_COL].nunique()
    stays_temp_saida = df.loc[df["temp"].notna(), "patient_id"].nunique()
    print(
        f"  Cru: {len(temp_cru):,} linhas com temperatura ({temp_cru.drop_duplicates(chave).shape[0]:,} instantes distintos) em {stays_temp_cru} internações"
    )
    print(
        f"  Saída: {int(df['temp'].notna().sum()):,} valores de temperatura em {stays_temp_saida} internações"
    )
    aud.check(
        stays_temp_cru == stays_temp_saida,
        "mesmas internações com temperatura no cru e na saída",
    )

    lo, hi = cfg.fahrenheit_range
    em_f = temp_cru[temp_cru["temp"].between(lo, hi)]
    print(
        f"\n  Conversão °F → °C: {len(em_f):,} linhas cruas em [{lo:g}, {hi:g}] (°F) em {em_f[STAY_COL].nunique()} internações"
    )
    lado_a_lado = em_f.drop_duplicates(chave).merge(
        esperado[[*chave, "patient_id", "timestamp"]], on=chave
    )
    lado_a_lado = lado_a_lado.merge(
        df[["patient_id", "timestamp", "temp"]],
        on=["patient_id", "timestamp"],
        suffixes=("_cru_F", "_saida_C"),
    )
    lado_a_lado["(F-32)*5/9"] = (lado_a_lado["temp_cru_F"] - 32) * 5 / 9
    erro_conv = float(
        (lado_a_lado["temp_saida_C"] - lado_a_lado["(F-32)*5/9"]).abs().max()
    )
    aud.check(
        erro_conv <= TOLERANCIA,
        f"todas as {len(lado_a_lado):,} temperaturas em °F saem como (F−32)·5/9 (erro máximo {erro_conv:.1e})",
    )
    aud.check(
        bool(df["temp"].max() <= 45),
        f"nenhuma temperatura na saída acima de 45 °C (máximo {df['temp'].max():.1f} °C)",
    )
    por_stay = max(1, args.examples // max(1, lado_a_lado["patient_id"].nunique()))
    exemplos = (
        lado_a_lado.sort_values(["patient_id", "timestamp"])
        .groupby("patient_id")
        .head(por_stay)
    )
    print(f"\n  Exemplos (até {por_stay} por internação):")
    print(
        exemplos[
            ["patient_id", "timestamp", "temp_cru_F", "temp_saida_C", "(F-32)*5/9"]
        ]
        .round(2)
        .to_string(index=False)
    )

    pid = f"{cfg.patient_id_prefix}{SERIE_COM_TEMPERATURA}"
    serie = df[(df["patient_id"] == pid)]
    com_temp = serie[serie["temp"].notna()]
    cru_serie = temp_cru[temp_cru[STAY_COL] == SERIE_COM_TEMPERATURA]
    if not com_temp.empty:
        horas = (
            com_temp["timestamp"].iloc[0] - serie["timestamp"].iloc[0]
        ) / pd.Timedelta(hours=1)
        print(
            f"\n  Série real com temperatura: {pid} — {len(serie)} linhas, {len(com_temp)} com temperatura"
        )
        print(
            f"  primeira temperatura em {com_temp['timestamp'].iloc[0]} (offset {int(cru_serie[OFFSET_COL].min())} min, {horas:.1f} h após o início da série)"
        )
        print(
            com_temp[["timestamp", *SINAIS]].head(args.examples).to_string(index=False)
        )
    aud.check(
        len(com_temp) == cru_serie.drop_duplicates(chave).shape[0],
        f"{pid}: temperaturas na saída == no cru ({len(cru_serie)})",
    )

    artefatos = int((df["temp"] < 30).sum())
    print(
        f"\n  [INFO] {artefatos} temperaturas < 30 °C mantidas como vieram (artefatos; limpeza é da próxima etapa)"
    )

    _titulo("E. Schema canônico")
    try:
        aud.check(
            validate_vitals(df) is df,
            "validate_vitals(df_completo): colunas, tipos, ordem e sem duplicatas",
        )
    except SchemaError as exc:
        aud.check(False, f"validate_vitals: {exc}")

    _titulo("Resumo da cobertura por sinal (dataset completo)")
    resumo = pd.DataFrame(
        {
            "internações com o sinal": (cont_saida > 0).sum(),
            "% das linhas com valor": (df[SINAIS].notna().mean() * 100).round(1),
        }
    )
    print(resumo.to_string())

    print(f"\nTempo: {time.perf_counter() - inicio:.1f} s")
    print(
        f"Resultado: {'TODAS AS VERIFICAÇÕES OK' if not aud.falhas else f'{len(aud.falhas)} FALHA(S)'}"
    )
    return 1 if aud.falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
