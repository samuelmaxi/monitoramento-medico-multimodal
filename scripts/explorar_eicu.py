"""Mostra o que a ingestão da US15 devolve para os dados reais do eICU Demo.

    python scripts/explorar_eicu.py
    python scripts/explorar_eicu.py --max-patients 3 --patient eicu_141765 --rows 40

Roda, em ordem, os passos B1–B4 e imprime cada resultado com o que se espera
ver, para conferir à mão contra a task: "séries de FC, PA sistólica/diastólica
e SpO2 processadas por paciente (FR e temperatura quando disponíveis)".
Não decide OK/FALHA; só mostra.

Pré-requisito: python scripts/download_data.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from vitals_anomaly import load_vitals
from vitals_anomaly.data.eicu import EICUDataError
from vitals_anomaly.schema import SchemaError, validate_vitals

SINAIS = ["hr", "sbp", "dbp", "spo2", "rr", "temp"]


def _titulo(texto: str) -> None:
    print(f"\n{'=' * 80}\n{texto}\n{'=' * 80}")


def _esperado(texto: str) -> None:
    print(f"\n→ Esperado: {texto}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-patients", type=int, default=10, help="quantas internações carregar (padrão: 10)")
    parser.add_argument("--patient", default=None, help="patient_id mostrado no B2 (padrão: o primeiro)")
    parser.add_argument("--rows", type=int, default=20, help="linhas da série mostradas no B2 (padrão: 20)")
    parser.add_argument("--data-dir", type=Path, default=None, help="pasta dos CSV (padrão: a do config)")
    args = parser.parse_args(argv)

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)

    _titulo("B1. Carregar algumas internações")
    print(f'df = load_vitals("eicu", max_patients={args.max_patients})')
    try:
        df = load_vitals("eicu", max_patients=args.max_patients, data_dir=args.data_dir)
    except EICUDataError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    print(f"\ndf.shape = {df.shape}  ({df.shape[0]} linhas × {df.shape[1]} colunas)")
    print(f"colunas  = {df.columns.tolist()}")
    print(f"\ndf['patient_id'].unique() = {df['patient_id'].unique().tolist()}")
    _esperado("uma série por internação, com patient_id no formato eicu_<patientunitstayid>.")

    pid = args.patient or df["patient_id"].iloc[0]
    p = df[df["patient_id"] == pid]
    if p.empty:  # --patient fora das N carregadas no B1: carrega só ela
        p = load_vitals("eicu", patient_ids=[pid], data_dir=args.data_dir)
    _titulo(f"B2. Uma série real: {pid}")
    if p.empty:
        print(f"patient_id {pid!r} não existe nos dados do eICU", file=sys.stderr)
        return 1
    print(f"{len(p)} linhas, de {p['timestamp'].min()} a {p['timestamp'].max()}\n")
    print(p[["timestamp", *SINAIS, "bp_source"]].head(args.rows).to_string(index=False))
    print("\nIntervalos mais comuns entre amostras:")
    print(p["timestamp"].diff().value_counts().head().to_string())
    _esperado(
        "linhas a cada 5 min (monitor) intercaladas com medidas de manguito; "
        "bp_source = invasive/noninvasive quando há PA, vazio quando não há."
    )

    com_temp = p[p["temp"].notna()]
    if com_temp.empty:
        print(f"\nTemperatura: {pid} não tem nenhuma medida de temperatura no eICU.")
    else:
        horas = (com_temp["timestamp"].iloc[0] - p["timestamp"].iloc[0]) / pd.Timedelta(hours=1)
        print(
            f"\nTemperatura: {len(com_temp)} medidas; a primeira em {com_temp['timestamp'].iloc[0]} "
            f"({horas:.1f} h após o início da série). Primeiras linhas com temperatura:\n"
        )
        print(com_temp[["timestamp", *SINAIS, "bp_source"]].head(args.rows).to_string(index=False))

    _titulo("B3. FC, PA e SpO2 por paciente; FR e temperatura só quando existem")
    contagem = df.groupby("patient_id")[SINAIS].count()
    print("Número de medidas de cada sinal, por internação:\n")
    print(contagem.to_string())
    print("\nInternações com pelo menos uma medida:")
    for s in SINAIS:
        print(f"  {s:5s}: {int((contagem[s] > 0).sum())} de {len(contagem)}")
    _esperado(
        "hr/sbp/dbp/spo2 presentes em quase todas as internações; contagem 0 significa que o eICU não "
        "mediu aquele sinal (a ingestão não inventa valores). rr aparece na maioria; temp é rara no "
        "vitalPeriodic (170 de 2.520 internações) → 'quando disponíveis'. "
        "Série com temperatura: --patient eicu_1034813 (começa ~35 h após a admissão); "
        "temperatura convertida de °F: --patient eicu_1578108. "
        "Verificação do dataset completo: python scripts/auditar_eicu.py."
    )

    _titulo("B4. Processamento aplicado (unidades e origem da PA)")
    print("df[sinais].describe().round(1):\n")
    print(df[SINAIS].describe().round(1).to_string())
    _esperado("FC em bpm, PA em mmHg, SpO2 ≤ 100 %, FR em irpm, temp em °C (convertida quando vinha em °F).")
    print("\ndf['bp_source'].value_counts(dropna=False):\n")
    print(df["bp_source"].value_counts(dropna=False).to_string())
    _esperado("invasiva (cateter) e não invasiva (manguito) unificadas em sbp/dbp; NaN = instante sem PA.")
    try:
        resultado = validate_vitals(df) is df
    except SchemaError as exc:
        resultado = f"SchemaError: {exc}"
    print(f"\nvalidate_vitals(df) is df → {resultado}")
    _esperado("True (colunas, tipos, ordem por paciente/tempo e sem duplicatas).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
