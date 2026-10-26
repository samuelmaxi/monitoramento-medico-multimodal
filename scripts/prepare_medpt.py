"""Baixa o MedPT e sorteia uma amostra de perguntas de pacientes no formato da US13.

    python scripts/prepare_medpt.py                    # 100 com termo crítico + 50 aleatórias
    python scripts/prepare_medpt.py --com-termo 200 --aleatorias 100 --seed 7

MedPT (AKCIT, LREC 2026): 384 mil pares pergunta–resposta reais de pacientes
brasileiros respondidos por médicos. https://huggingface.co/datasets/AKCIT/MedPT
O card não informa licença; por isso o texto fica fora do git e só as
anotações (IDs + rótulos) são versionadas.

A amostra é estratificada para a avaliação ter casos úteis:

- ``--com-termo`` perguntas cujo texto contém um sinônimo de algum termo crítico
  (``audio/text/critical_terms.yaml``), repartidas igualmente entre os termos;
- ``--aleatorias`` perguntas sorteadas do resto, como controle de falso positivo.

Cada pergunta vira uma transcrição com um único segmento do paciente
(``data/medpt/transcripts/medpt-<id>.json``). ``data/medpt/amostra.csv`` lista a
amostra com o estrato, para a anotação. Requer ``pyarrow`` para ler o parquet.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

import pandas as pd
import requests

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from audio.text.config import load_critical_terms  # noqa: E402
from audio.text.normalize import contains_phrase, normalize  # noqa: E402

URL = "https://huggingface.co/datasets/AKCIT/MedPT/resolve/main/data/train-00000-of-00001.parquet"


def baixar(destino: Path) -> Path:
    if not destino.is_file():
        destino.parent.mkdir(parents=True, exist_ok=True)
        resp = requests.get(URL, timeout=600)
        resp.raise_for_status()
        destino.write_bytes(resp.content)
    return destino


def termos_citados(texto: str, sinonimos: dict[str, list[str]]) -> list[str]:
    """Termos críticos cujo nome ou sinônimo aparece no texto (palavra inteira)."""
    texto = normalize(texto)
    return [termo for termo, lista in sinonimos.items() if any(contains_phrase(texto, s) for s in lista)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", type=Path, default=Path("data/medpt"))
    parser.add_argument("--terms", type=Path, default=Path("audio/text/critical_terms.yaml"))
    parser.add_argument("--com-termo", type=int, default=100)
    parser.add_argument("--aleatorias", type=int, default=50)
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--max-chars", type=int, default=600, help="descarta perguntas mais longas (anotação manual)")
    args = parser.parse_args(argv)

    parquet = baixar(args.dest / "raw" / "train-00000-of-00001.parquet")
    df = pd.read_parquet(parquet, columns=["id", "question", "condition", "medical_specialty", "question_type"])
    # A mesma pergunta aparece uma vez por resposta; fica a primeira.
    df = df.drop_duplicates("question").sort_values("id")
    df = df[df.question.str.len() <= args.max_chars]

    lista = load_critical_terms(args.terms)
    sinonimos = {t.name: sorted({normalize(s) for s in (t.name, *t.synonyms)}) for t in lista.terms}
    df = df.assign(termos=df.question.map(lambda q: termos_citados(q, sinonimos)))

    rng = random.Random(args.seed)
    escolhidos: dict[int, str] = {}
    por_termo = max(1, args.com_termo // len(sinonimos))
    for termo in sinonimos:
        ids = [i for i, ts in zip(df.id, df.termos) if termo in ts and i not in escolhidos]
        for i in rng.sample(ids, min(por_termo, len(ids))):
            escolhidos[i] = f"cita:{termo}"
    resto = [i for i, ts in zip(df.id, df.termos) if not ts and i not in escolhidos]
    for i in rng.sample(resto, min(args.aleatorias, len(resto))):
        escolhidos[i] = "aleatoria"

    amostra = df[df.id.isin(escolhidos)]
    saida = args.dest / "transcripts"
    saida.mkdir(parents=True, exist_ok=True)
    for antigo in saida.glob("medpt-*.json"):
        antigo.unlink()
    with (args.dest / "amostra.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["audio_id", "estrato", "termos_citados", "condition", "question_type", "question"])
        for linha in amostra.itertuples():
            audio_id = f"medpt-{linha.id}"
            w.writerow([audio_id, escolhidos[linha.id], "; ".join(linha.termos), linha.condition,
                        linha.question_type, linha.question])
            (saida / f"{audio_id}.json").write_text(json.dumps({
                "audio_id": audio_id,
                "patient_id": f"medpt-paciente-{linha.id}",
                # Fictício: o MedPT não informa data. Só compõe o timestamp exigido pelo contrato.
                "recorded_at": "2025-01-01T00:00:00Z",
                "is_synthetic": False,
                "language": "pt-BR",
                "source": f"AKCIT/MedPT id={linha.id}",
                "segments": [{"speaker": "paciente", "text": linha.question, "offset_ms": 0,
                              "duration_ms": 0, "confidence": None}],
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    estratos = pd.Series(list(escolhidos.values())).value_counts()
    print(f"{len(amostra)} pergunta(s) em {saida} (seed {args.seed})")
    print(estratos.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
