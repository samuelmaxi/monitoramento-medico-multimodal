"""Baixa as tabelas de sinais vitais do MIMIC-IV Demo (PhysioNet, acesso aberto)."""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ARQUIVOS = ("chartevents.csv.gz", "d_items.csv.gz")


def main() -> int:
    raiz = Path(__file__).resolve().parent.parent
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))
    from sinais import mimic

    destino = mimic.CHARTEVENTS.parent
    destino.mkdir(parents=True, exist_ok=True)
    for nome in ARQUIVOS:
        alvo = destino / nome
        if alvo.exists():
            print(f"já existe: {alvo}")
            continue
        print(f"baixando {mimic.URL_BASE}/{nome} → {alvo}")
        parcial = alvo.with_suffix(".parcial")
        urllib.request.urlretrieve(f"{mimic.URL_BASE}/{nome}", parcial)
        parcial.rename(alvo)  # não deixa arquivo truncado com o nome final
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
