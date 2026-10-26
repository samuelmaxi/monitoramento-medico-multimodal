"""Baixa uma amostra reprodutível do dataset Eka Medical ASR do Hugging Face (US11).

Em vez de baixar o parquet inteiro (~237 MB), usa a API `datasets-server` para
ler só as linhas sorteadas. A seleção usa semente fixa, então a mesma
`seed` e `n` sempre trazem os mesmos áudios.

Para cada linha sorteada são gravados, em `destino/`:
- `<id>.wav`: áudio WAV 16 kHz mono, pronto para a transcrição;
- `<id>.txt`: transcrição de referência do dataset;
- `origem/<id>.m4a`: arquivo original, sem alterações;
- `manifest.json`: metadados de todas as amostras.

Arquivos já existentes não são baixados de novo.
"""

import hashlib
import json
import random
from pathlib import Path

import httpx

from app.audio_convert import converter_para_wav

DATASET = "ekacare/eka-medical-asr-evaluation-dataset"
SPLIT = "test"
_BASE_URL = "https://datasets-server.huggingface.co"


def baixar_amostra(
    n: int,
    *,
    destino: Path,
    config: str = "en",
    seed: int = 42,
    transporte: httpx.BaseTransport | None = None,
) -> list[tuple[Path, Path]]:
    """Garante `n` pares (wav, txt) em `destino` e devolve os pares em ordem de índice."""
    if n <= 0:
        raise ValueError("n deve ser maior que zero.")

    with httpx.Client(timeout=60.0, transport=transporte, follow_redirects=True) as cliente:
        total = _ler_linha(cliente, config, 0)["num_rows_total"]
        if n > total:
            raise ValueError(f"A config '{config}' tem {total} linhas; não dá para amostrar {n}.")

        indices = sorted(random.Random(seed).sample(range(total), n))
        destino.mkdir(parents=True, exist_ok=True)
        (destino / "origem").mkdir(exist_ok=True)

        pares: list[tuple[Path, Path]] = []
        entradas: list[dict] = []
        for indice in indices:
            linha = _ler_linha(cliente, config, indice)
            id_amostra = f"hf_{config}_{indice:05d}"
            wav = destino / f"{id_amostra}.wav"
            txt = destino / f"{id_amostra}.txt"

            origem_bytes = None
            if not wav.is_file():
                origem_bytes = _baixar_audio(cliente, linha["audio"][0]["src"])
                origem = destino / "origem" / f"{id_amostra}.m4a"
                origem.write_bytes(origem_bytes)
                converter_para_wav(origem, wav)
            txt.write_text(linha["text"], encoding="utf-8")

            pares.append((wav, txt))
            entradas.append(
                {
                    "id": id_amostra,
                    "indice": indice,
                    "config": config,
                    "session_id": linha.get("session_id"),
                    "file_name": linha.get("file_name"),
                    "duracao_s": linha.get("duration"),
                    "idioma_audio": linha.get("audio_language"),
                    "idioma_texto": linha.get("text_language"),
                    "md5_origem": hashlib.md5(origem_bytes).hexdigest() if origem_bytes else None,
                    "md5_hf_informado": linha.get("md5_audio"),
                }
            )

    _gravar_manifesto(destino, dataset=DATASET, config=config, seed=seed, entradas=entradas)
    return pares


def _ler_linha(cliente: httpx.Client, config: str, indice: int) -> dict:
    resposta = cliente.get(
        f"{_BASE_URL}/rows",
        params={
            "dataset": DATASET,
            "config": config,
            "split": SPLIT,
            "offset": indice,
            "length": 1,
        },
    )
    resposta.raise_for_status()
    corpo = resposta.json()
    linha = corpo["rows"][0]["row"]
    linha["num_rows_total"] = corpo["num_rows_total"]
    return linha


def _baixar_audio(cliente: httpx.Client, url: str) -> bytes:
    resposta = cliente.get(url)
    resposta.raise_for_status()
    return resposta.content


def _gravar_manifesto(destino: Path, *, dataset: str, config: str, seed: int, entradas: list[dict]) -> None:
    documento = {
        "dataset": dataset,
        "config": config,
        "split": SPLIT,
        "seed": seed,
        "amostras": entradas,
    }
    (destino / "manifest.json").write_text(
        json.dumps(documento, ensure_ascii=False, indent=2), encoding="utf-8"
    )
