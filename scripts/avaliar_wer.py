"""Mede o WER da transcrição contra as transcrições manuais de referência (US11).

Cada áudio precisa de um .txt de mesmo nome com a transcrição manual:

    dados/transcricao/consulta_01.wav
    dados/transcricao/consulta_01.txt

    python scripts/avaliar_wer.py                       # sem phrase list
    python scripts/avaliar_wer.py --frases-medicas      # com phrase list (se WER > 20%)

Saídas em saida/transcricao/avaliacao/<sem_frases|com_frases>/: um JSON por
áudio, wer.json e relatorio_wer.md (WER e amostra de transcrição, para o relatório).
Os JSONs já gerados são reaproveitados, então rodar de novo não repete chamadas.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.config import (  # noqa: E402
    require_azure_speech_endpoint,
    require_azure_speech_key,
)
from app.transcription_client import FORMATOS_SUPORTADOS, TranscricaoError  # noqa: E402
from app.transcription_service import transcrever_e_salvar  # noqa: E402
from app.wer import ResultadoWER, calcular_wer, classificar_wer, wer_agregado  # noqa: E402

MINIMO_AUDIOS = 10
LIMITE_WER_PHRASE_LIST = 0.20
TAMANHO_AMOSTRA = 500


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, default=RAIZ / "dados" / "transcricao")
    parser.add_argument("--frases-medicas", action="store_true")
    parser.add_argument("--max-falantes", type=int, default=2)
    parser.add_argument("--saida", type=Path, default=RAIZ / "saida" / "transcricao" / "avaliacao")
    args = parser.parse_args(argv)

    pares = _pares(args.dir)
    if not pares:
        print(f"[ERRO] Nenhum áudio com .txt de referência em '{args.dir}'.", file=sys.stderr)
        return 1
    if len(pares) < MINIMO_AUDIOS:
        print(
            f"[AVISO] {len(pares)} áudio(s) com referência; o DoD pede ao menos {MINIMO_AUDIOS}.",
            file=sys.stderr,
        )

    try:
        endpoint = require_azure_speech_endpoint()
        chave = require_azure_speech_key()
    except RuntimeError as exc:
        print(f"[ERRO] {exc}", file=sys.stderr)
        return 1

    execucao = "com_frases" if args.frases_medicas else "sem_frases"
    pasta = args.saida / execucao

    itens: list[dict] = []
    erros: list[dict] = []
    for audio, referencia_path in pares:
        referencia = referencia_path.read_text(encoding="utf-8")
        try:
            resultado = transcrever_e_salvar(
                audio,
                pasta,
                endpoint=endpoint,
                chave=chave,
                usar_frases=args.frases_medicas,
                max_speakers=args.max_falantes,
            )
        except (TranscricaoError, OSError) as exc:
            erros.append({"audio": audio.name, "erro": str(exc)})
            print(f"[ERRO] {audio.name}: {exc}", file=sys.stderr)
            continue

        hipotese = resultado["texto_completo"]
        itens.append(
            {
                "audio": audio.name,
                "resultado": calcular_wer(referencia, hipotese),
                "referencia": referencia,
                "hipotese": hipotese,
            }
        )
        print(f"{audio.name}: WER {itens[-1]['resultado'].wer:.1%}")

    if not itens:
        print("[ERRO] Nenhum áudio transcrito com sucesso; nada a medir.", file=sys.stderr)
        return 1

    relatorio = _montar_relatorio(execucao, args.frases_medicas, itens, erros)
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "wer.json").write_text(
        json.dumps(relatorio["json"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (pasta / "relatorio_wer.md").write_text(relatorio["markdown"], encoding="utf-8")

    wer_global = relatorio["json"]["wer_global"]
    print(f"WER global ({execucao}): {wer_global:.1%} - {classificar_wer(wer_global)}")
    print(f"Relatório: {pasta / 'relatorio_wer.md'}")
    return 0


def _pares(pasta: Path) -> list[tuple[Path, Path]]:
    if not pasta.is_dir():
        return []
    pares = []
    for audio in sorted(pasta.iterdir()):
        referencia = audio.with_suffix(".txt")
        if audio.suffix.lower().lstrip(".") in FORMATOS_SUPORTADOS and referencia.is_file():
            pares.append((audio, referencia))
    return pares


def _montar_relatorio(execucao: str, frases: bool, itens: list[dict], erros: list[dict]) -> dict:
    resultados: list[ResultadoWER] = [item["resultado"] for item in itens]
    wer_global = wer_agregado(resultados)
    wer_media = sum(r.wer for r in resultados) / len(resultados)

    json_doc = {
        "execucao": execucao,
        "frases_medicas": frases,
        "criado_em": datetime.now(timezone.utc).isoformat(),
        "n_audios": len(itens),
        "minimo_audios_dod": MINIMO_AUDIOS,
        "wer_global": wer_global,
        "wer_media_simples": wer_media,
        "classificacao": classificar_wer(wer_global),
        "audios": [
            {
                "audio": item["audio"],
                "wer": r.wer,
                "palavras_referencia": r.palavras_referencia,
                "substituicoes": r.substituicoes,
                "delecoes": r.delecoes,
                "insercoes": r.insercoes,
            }
            for item, r in zip(itens, resultados)
        ],
        "erros": erros,
    }
    return {"json": json_doc, "markdown": _markdown(json_doc, itens)}


def _markdown(doc: dict, itens: list[dict]) -> str:
    linhas = [
        f"# Avaliação de WER - transcrição Azure AI Speech ({doc['execucao']})",
        "",
        f"- Áudios avaliados: **{doc['n_audios']}** (mínimo do DoD: {MINIMO_AUDIOS})",
        f"- Phrase list: {'sim' if doc['frases_medicas'] else 'não'}",
        f"- **WER global: {doc['wer_global']:.1%}** - {doc['classificacao']}",
        f"- WER médio por áudio: {doc['wer_media_simples']:.1%}",
        "- Limite obrigatório: 20% (aceitável). Meta desejável: 5 a 10%. 30% ou mais: não aceito.",
        "",
    ]
    if doc["n_audios"] < MINIMO_AUDIOS:
        linhas += [f"> Atenção: a amostra tem menos de {MINIMO_AUDIOS} áudios.", ""]
    if not doc["frases_medicas"] and doc["wer_global"] > LIMITE_WER_PHRASE_LIST:
        linhas += [
            "> WER acima de 20%: rodar de novo com `--frases-medicas` e comparar.",
            "",
        ]

    linhas += [
        "## WER por áudio",
        "",
        "| Áudio | Palavras (ref.) | WER | Substituições | Deleções | Inserções |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for audio in doc["audios"]:
        linhas.append(
            f"| {audio['audio']} | {audio['palavras_referencia']} | {audio['wer']:.1%} "
            f"| {audio['substituicoes']} | {audio['delecoes']} | {audio['insercoes']} |"
        )

    if doc["erros"]:
        linhas += ["", "## Áudios com falha na API", ""]
        linhas += [f"- {erro['audio']}: {erro['erro']}" for erro in doc["erros"]]

    primeiro = itens[0]
    linhas += [
        "",
        "## Amostra de transcrição",
        "",
        f"Áudio: `{primeiro['audio']}` (trechos de até {TAMANHO_AMOSTRA} caracteres).",
        "",
        "**Referência manual:**",
        "",
        f"> {_trecho(primeiro['referencia'])}",
        "",
        "**Transcrição (Azure AI Speech):**",
        "",
        f"> {_trecho(primeiro['hipotese'])}",
        "",
        "## Normalização",
        "",
        "Minúsculas, sem pontuação, espaços colapsados. Números em algarismos e por extenso contam como erros diferentes.",
        "",
    ]
    return "\n".join(linhas)


def _trecho(texto: str) -> str:
    texto = " ".join(texto.split())
    if len(texto) <= TAMANHO_AMOSTRA:
        return texto
    return texto[:TAMANHO_AMOSTRA] + "..."


if __name__ == "__main__":
    sys.exit(main())
