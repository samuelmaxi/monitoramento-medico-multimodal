"""Transcreve áudios com o Azure AI Speech (pt-BR, diarização) e grava um JSON por áudio.

    python scripts/transcrever.py consulta_01.wav consulta_02.mp3
    python scripts/transcrever.py consulta_01.wav --frases-medicas --medico-falante 1
    python scripts/transcrever.py consulta_01.wav --medico-falante 1 \
        --patient-id 10000032 --recorded-at 2026-10-01T13:00:00Z

O JSON vai para saida/transcricao/<nome>.json. Com --medico-falante,
--patient-id e --recorded-at ele já serve de entrada para a US13
(python -m audio.text.run --input saida/transcricao/consulta_01.json ...). Se ele já existir para o mesmo
áudio e as mesmas opções, a API não é chamada de novo.
"""

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.config import (  # noqa: E402
    TRANSCRIPTION_OUTPUT_DIR,
    require_azure_speech_endpoint,
    require_azure_speech_key,
)
from app.transcription_client import TranscricaoError  # noqa: E402
from app.transcription_service import transcrever_e_salvar  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("audios", nargs="+", type=Path, help="Arquivos de áudio a transcrever.")
    parser.add_argument(
        "--frases-medicas",
        action="store_true",
        help="Envia a phrase list de termos clínicos (usar só se o WER passar de 20%%).",
    )
    parser.add_argument("--max-falantes", type=int, default=2, help="Máximo de falantes na diarização.")
    parser.add_argument("--locale", default="pt-BR", help="Idioma da transcrição (padrão: pt-BR).")
    parser.add_argument(
        "--medico-falante",
        type=int,
        help="Número do falante que é o médico; os demais viram 'paciente'.",
    )
    parser.add_argument("--patient-id", help="ID de origem ou pseudônimo pt_… do paciente.")
    parser.add_argument("--recorded-at", help="Início da gravação, ISO-8601 UTC (ex.: 2026-10-01T13:00:00Z).")
    parser.add_argument(
        "--audio-id",
        help="ID curto da consulta, sem dados pessoais (só com um áudio). Padrão: nome do arquivo.",
    )
    parser.add_argument("--saida", type=Path, default=Path(TRANSCRIPTION_OUTPUT_DIR))
    args = parser.parse_args(argv)
    if args.audio_id and len(args.audios) > 1:
        parser.error("--audio-id só pode ser usado com um áudio por vez")

    try:
        endpoint = require_azure_speech_endpoint()
        chave = require_azure_speech_key()
    except RuntimeError as exc:
        print(f"[ERRO] {exc}", file=sys.stderr)
        return 1

    status = 0
    for audio in args.audios:
        try:
            resultado = transcrever_e_salvar(
                audio,
                args.saida,
                endpoint=endpoint,
                chave=chave,
                usar_frases=args.frases_medicas,
                max_speakers=args.max_falantes,
                medico_falante=args.medico_falante,
                locale=args.locale,
                audio_id=args.audio_id,
                patient_id=args.patient_id,
                recorded_at=args.recorded_at,
            )
        except (TranscricaoError, OSError, ValueError) as exc:
            print(f"[ERRO] {audio}: {exc}", file=sys.stderr)
            status = 1
            continue

        destino = args.saida / f"{audio.stem}.json"
        print(f"{audio.name}: {len(resultado['falas'])} falas -> {destino}")
    return status


if __name__ == "__main__":
    sys.exit(main())
