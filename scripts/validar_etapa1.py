"""Valida, sem pytest, que a ingestão da US15 entrega as séries pedidas.

    python scripts/validar_etapa1.py                          # fonte sintética
    python scripts/validar_etapa1.py --source eicu --max-patients 5
    python scripts/validar_etapa1.py --source eicu --patient eicu_141765
    python scripts/validar_etapa1.py --export saida/sinais    # um CSV por paciente

Chama ``load_vitals`` e confere cada item do critério da task: "séries de FC,
PA sistólica/diastólica e SpO2 processadas por paciente (FR e temperatura
quando disponíveis)". Termina com código 1 se alguma verificação falhar.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from vitals_anomaly import load_vitals
from vitals_anomaly.schema import OPTIONAL_VITALS, REQUIRED_VITALS, SchemaError, validate_vitals

NOMES = {
    "hr": "FC (bpm)",
    "sbp": "PAS (mmHg)",
    "dbp": "PAD (mmHg)",
    "spo2": "SpO2 (%)",
    "rr": "FR (irpm)",
    "temp": "Temp (°C)",
}


def _check(ok: bool, texto: str, falhas: list[str]) -> None:
    print(f"  [{'OK' if ok else 'FALHA'}] {texto}")
    if not ok:
        falhas.append(texto)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=["synthetic", "eicu"], default="synthetic")
    parser.add_argument("--max-patients", type=int, default=None, help="limita o número de pacientes")
    parser.add_argument("--patient", default=None, help="mostra a série completa deste patient_id")
    parser.add_argument("--export", type=Path, default=None, help="pasta para gravar um CSV por paciente")
    args = parser.parse_args(argv)

    df = load_vitals(args.source, max_patients=args.max_patients)
    sinais = list(REQUIRED_VITALS + OPTIONAL_VITALS)
    falhas: list[str] = []

    print(f"\nFonte: {args.source} — {df['patient_id'].nunique()} pacientes, {len(df)} linhas")
    print(f"Período: {df['timestamp'].min()} → {df['timestamp'].max()}\n")

    print("1. Formato canônico")
    try:
        validate_vitals(df)
        _check(True, "colunas, tipos, ordenação por paciente/tempo e sem duplicatas", falhas)
    except SchemaError as exc:
        _check(False, f"schema: {exc}", falhas)

    print("\n2. Séries obrigatórias por paciente (FC, PAS, PAD, SpO2)")
    contagem = df.groupby("patient_id")[sinais].count()
    for s in REQUIRED_VITALS:
        sem = contagem.index[contagem[s] == 0].tolist()
        _check(not sem, f"{NOMES[s]} presente em todos os pacientes" + (f" (faltando em {sem})" if sem else ""), falhas)

    print("\n3. Séries opcionais (FR, temperatura) — só informativo")
    for s in OPTIONAL_VITALS:
        com = int((contagem[s] > 0).sum())
        print(f"  [INFO] {NOMES[s]} disponível em {com} de {len(contagem)} pacientes")

    print("\n4. Unidades plausíveis (medianas)")
    med = df[sinais].median()
    _check(30 <= med["hr"] <= 200, f"FC mediana {med['hr']:.0f} bpm", falhas)
    _check(60 <= med["sbp"] <= 220 and 30 <= med["dbp"] <= 130, f"PA mediana {med['sbp']:.0f}/{med['dbp']:.0f} mmHg", falhas)
    _check(df["spo2"].max() <= 100, f"SpO2 mediana {med['spo2']:.0f}%, máximo {df['spo2'].max():.0f}% (≤ 100)", falhas)
    if pd.notna(med["temp"]):
        _check(30 <= med["temp"] <= 43, f"temperatura mediana {med['temp']:.1f} °C (em Celsius)", falhas)

    print("\n5. Resumo por paciente (n = medidas, média)")
    resumo = df.groupby("patient_id")[sinais].agg(["count", "mean"]).round(1)
    resumo.columns = [f"{NOMES[s].split()[0]} {'n' if a == 'count' else 'média'}" for s, a in resumo.columns]
    print(resumo.head(10).to_string())
    if len(resumo) > 10:
        print(f"  ... (+{len(resumo) - 10} pacientes)")

    paciente = args.patient or df["patient_id"].iloc[0]
    serie = df[df["patient_id"] == paciente]
    if serie.empty:
        print(f"\npatient_id {paciente!r} não encontrado", file=sys.stderr)
        return 1
    print(f"\n6. Série de {paciente} ({len(serie)} linhas; primeiras 10)")
    print(serie.head(10).to_string(index=False))

    if args.export:
        args.export.mkdir(parents=True, exist_ok=True)
        for pid, grupo in df.groupby("patient_id"):
            grupo.to_csv(args.export / f"{pid}.csv", index=False)
        print(f"\n{df['patient_id'].nunique()} CSVs gravados em {args.export}/")

    print(f"\nResultado: {'TODAS AS VERIFICAÇÕES OK' if not falhas else f'{len(falhas)} FALHA(S)'}")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
