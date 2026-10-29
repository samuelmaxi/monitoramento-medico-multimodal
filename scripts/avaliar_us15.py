"""Avalia a US15 com anomalias injetadas e marcadas sobre o MIMIC-IV Demo.

Pacientes com subject_id par formam o conjunto de ajuste (onde os limiares
foram escolhidos); os ímpares, o conjunto de teste, que é o número reportado.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    raiz = Path(__file__).resolve().parent.parent
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))

    from dotenv import load_dotenv

    from contratos import pseudonimizar_paciente
    from sinais import ConfigDetector, mimic
    from sinais.avaliacao import avaliar, detectar, injetar, matriz, montar_rotulos

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chartevents", type=Path, default=mimic.CHARTEVENTS)
    parser.add_argument("--saida", type=Path, default=Path("saida/sinais"))
    parser.add_argument("--semente", type=int, default=42)
    parser.add_argument("--limiar-z", type=float, default=ConfigDetector.limiar_z)
    parser.add_argument("--consecutivas", type=int, default=ConfigDetector.medicoes_consecutivas)
    parser.add_argument("--limiar-tendencia", type=float, default=ConfigDetector.limiar_tendencia)
    args = parser.parse_args(argv)

    load_dotenv()
    if not args.chartevents.exists():
        parser.error(f"{args.chartevents} não existe; rode scripts/baixar_sinais.py")
    config = ConfigDetector(
        limiar_z=args.limiar_z,
        medicoes_consecutivas=args.consecutivas,
        limiar_tendencia=args.limiar_tendencia,
    )

    df = mimic.carregar(args.chartevents)
    pseudonimos = {s: pseudonimizar_paciente(s) for s in df.subject_id.unique()}
    rotulos = montar_rotulos(df, args.semente)
    eventos, detector, ms = detectar(injetar(df, rotulos), pseudonimos, config)
    resultado = avaliar(rotulos, eventos, pseudonimos)

    def resumo(r):
        return matriz(r).para_dict() | {
            "por_tipo": {t or "negativo": matriz(g).para_dict() for t, g in r.groupby("tipo")},
            "por_sinal": {s: matriz(g).para_dict() for s, g in r.groupby("sinal")},
        }

    relatorio = {
        "config": {
            k: v.total_seconds() / 3600 if isinstance(v, timedelta) else v
            for k, v in asdict(config).items()
        },
        "semente": args.semente,
        "medicoes": len(df),
        "pacientes": len(pseudonimos),
        "ms_por_medicao": round(ms, 4),
        "artefatos_descartados": dict(detector.artefatos),
        "eventos_por_tipo": dict(Counter(e.event_type for e in eventos)),
        "ajuste": resumo(resultado[resultado.subject_id % 2 == 0]),
        "teste": resumo(resultado[resultado.subject_id % 2 == 1]),
    }
    args.saida.mkdir(parents=True, exist_ok=True)
    resultado.assign(subject_id=resultado.subject_id.map(pseudonimos)).rename(
        columns={"subject_id": "patient_id"}
    ).to_csv(args.saida / "rotulos_us15.csv", index=False)
    (args.saida / "relatorio_us15.json").write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2)
    )
    print(
        json.dumps(
            {
                k: relatorio[k]
                for k in ("ms_por_medicao", "artefatos_descartados", "eventos_por_tipo")
            }
            | {
                c: {k: relatorio[c][k] for k in ("matriz_confusao", "recall", "precisao", "f1")}
                for c in ("ajuste", "teste")
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
