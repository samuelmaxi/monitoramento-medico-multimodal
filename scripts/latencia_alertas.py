"""Relatório de latência dos alertas (US19): do dado bruto até o primeiro envio.

    python scripts/latencia_alertas.py saida/alertas.jsonl

A meta do DoD é p95 ≤ 10 s. Sai com código 2 se a meta não for atendida.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from alertas.latencia import latencias_segundos, ler_alertas, percentil  # noqa: E402

META_P95_S = 10.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("caminho", nargs="?", default="saida/alertas.jsonl")
    args = parser.parse_args(argv)

    alertas = ler_alertas(args.caminho)
    medidas = latencias_segundos(alertas)
    if not medidas:
        print(f"nenhum alerta enviado em {args.caminho}")
        return 1

    bases = Counter(a.latency_base for a in alertas if a.sent_at is not None)
    p95 = percentil(medidas, 95)
    print(f"alertas enviados: {len(medidas)}")
    print(f"base da medida: {dict(bases)} (ingested_at quando existe, senão detected_at)")
    print(f"p50: {percentil(medidas, 50):.3f} s")
    print(f"p95: {p95:.3f} s")
    print(f"máximo: {max(medidas):.3f} s")
    atendida = p95 <= META_P95_S
    print(f"meta p95 ≤ {META_P95_S:g} s: {'atendida' if atendida else 'NÃO atendida'}")
    return 0 if atendida else 2


if __name__ == "__main__":
    sys.exit(main())
