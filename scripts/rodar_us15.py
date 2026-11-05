"""Replay em "tempo real" do MIMIC-IV Demo pelo detector de sinais vitais (US15).

As medições chegam uma a uma, na ordem de tempo, deslocadas para o instante da
execução. ``--velocidade 60`` faz 1 h de dados passar em 1 min; ``0`` não espera.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    raiz = Path(__file__).resolve().parent.parent
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))

    from dotenv import load_dotenv

    from contratos import EmissorJsonl, pseudonimizar_paciente
    from sinais import DetectorSinais, Medicao, mimic

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chartevents", type=Path, default=mimic.CHARTEVENTS)
    parser.add_argument("--saida", type=Path, default=Path("saida/sinais/eventos_us15.jsonl"))
    parser.add_argument(
        "--velocidade", type=float, default=0.0, help="fator de aceleração; 0 = sem espera"
    )
    parser.add_argument("--pacientes", type=int, help="limita aos N primeiros subject_id")
    args = parser.parse_args(argv)

    load_dotenv()
    if not args.chartevents.exists():
        parser.error(f"{args.chartevents} não existe; rode scripts/baixar_sinais.py")
    df = mimic.carregar(args.chartevents)
    if args.pacientes:
        df = df[df.subject_id.isin(sorted(df.subject_id.unique())[: args.pacientes])]
    pseudonimos = {s: pseudonimizar_paciente(s) for s in df.subject_id.unique()}

    emissor = EmissorJsonl(args.saida)
    detector = DetectorSinais()
    contagem: Counter[str] = Counter()
    # O MIMIC desloca as datas de cada paciente para anos diferentes: todos passam a começar agora.
    inicio = df.groupby("subject_id").instante.transform("min")
    df = df.assign(instante=datetime.now(UTC) + (df.instante - inicio)).sort_values(
        "instante", kind="stable"
    )
    anterior = None
    for linha in df.itertuples():
        instante = linha.instante.to_pydatetime()
        if args.velocidade and anterior is not None:
            time.sleep((instante - anterior).total_seconds() / args.velocidade)
        anterior = instante
        eventos = detector.processar(
            Medicao(pseudonimos[linha.subject_id], instante, linha.sinal, linha.valor)
        )
        emissor.emitir_varios(eventos)
        for e in eventos:
            contagem[e.event_type] += 1
            if args.velocidade:
                print(
                    f"{e.timestamp:%H:%M:%S} {e.severity.value:5} {e.evidence.summary}", flush=True
                )
    print(f"{len(df)} medições, artefatos descartados: {dict(detector.artefatos)}")
    print(f"eventos: {dict(contagem)} → {args.saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
