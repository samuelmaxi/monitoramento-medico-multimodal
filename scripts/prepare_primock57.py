"""Baixa o PriMock57 e converte as transcrições manuais para o formato da US13.

    python scripts/prepare_primock57.py              # transcrições e notas
    python scripts/prepare_primock57.py --audio      # também os .wav (≈ 9 h, via Git LFS)

PriMock57 (Babylon Health, ACL 2022, CC BY 4.0): 57 consultas simuladas de
atenção primária em inglês, com atores como pacientes. Cada consulta tem um
canal de áudio e uma transcrição manual (TextGrid) por falante, além da nota
escrita pelo médico. https://github.com/babylonhealth/primock57

Saída em ``data/primock57/`` (fora do git):

- ``raw/transcripts/*.TextGrid`` e ``raw/notes/*.json``: arquivos originais;
- ``raw/audio/*.wav``: só com ``--audio``;
- ``transcripts/primock57-dXcYY.json``: transcrição no formato de
  ``audio/text/transcript.py``, com médico e paciente intercalados pelo tempo.

O ``recorded_at`` é fictício (o dataset não informa a data): dia X da coleta
vira ``2022-01-0X``. ``is_synthetic`` é ``true`` porque são consultas simuladas.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# requests (dependência do gdown) usa os certificados do certifi; o urllib do
# Python do python.org no macOS não encontra a cadeia de certificados.
import requests

REPO = "babylonhealth/primock57"
RAW = f"https://raw.githubusercontent.com/{REPO}/main"
# Os .wav estão no Git LFS; o endpoint "media" devolve o binário sem precisar do git-lfs.
MEDIA = f"https://media.githubusercontent.com/media/{REPO}/main"
API = f"https://api.github.com/repos/{REPO}/contents"

_INTERVALO = re.compile(r'xmin = ([\d.]+)\s*\n\s*xmax = ([\d.]+)\s*\n\s*text = "((?:[^"]|"")*)"')
_NOME = re.compile(r"day(\d)_consultation(\d+)_(doctor|patient)\.TextGrid")
_TAG_UNSURE = re.compile(r"</?UNSURE>")
# Marcas de trecho ininteligível; somem do texto.
_TAG_UNIN = re.compile(r"<(UNIN|INAUDIBLE_SPEECH)\s*/>")
_ESPACOS = re.compile(r"\s+")

FALANTE = {"doctor": "medico", "patient": "paciente"}


def _baixar(url: str, destino: Path) -> None:
    if destino.is_file() and destino.stat().st_size > 0:
        return
    destino.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    destino.write_bytes(resp.content)


def _listar(pasta: str) -> list[str]:
    resp = requests.get(f"{API}/{pasta}", timeout=60)
    resp.raise_for_status()
    return [item["name"] for item in resp.json() if item["type"] == "file"]


def limpar_texto(texto: str) -> str:
    """Tira as marcas do transcritor: ``<UNSURE>x</UNSURE>`` → ``x``; ``<UNIN/>`` e ``<INAUDIBLE_SPEECH/>`` somem."""
    texto = _TAG_UNIN.sub(" ", _TAG_UNSURE.sub("", texto.replace('""', '"')))
    return _ESPACOS.sub(" ", texto).strip()


def ler_textgrid(caminho: Path) -> list[tuple[float, float, str]]:
    """Intervalos ``(início_s, fim_s, texto)`` com texto não vazio."""
    conteudo = caminho.read_text(encoding="utf-8")
    intervalos = []
    for xmin, xmax, texto in _INTERVALO.findall(conteudo):
        texto = limpar_texto(texto)
        if texto:
            intervalos.append((float(xmin), float(xmax), texto))
    return intervalos


def converter(raw_dir: Path, dia: int, consulta: int) -> dict:
    base = f"day{dia}_consultation{consulta:02d}"
    segmentos = []
    for canal, falante in FALANTE.items():
        for inicio, fim, texto in ler_textgrid(raw_dir / "transcripts" / f"{base}_{canal}.TextGrid"):
            segmentos.append({
                "speaker": falante,
                "text": texto,
                "offset_ms": round(inicio * 1000),
                "duration_ms": max(0, round((fim - inicio) * 1000)),
                "confidence": None,
            })
    segmentos.sort(key=lambda s: (s["offset_ms"], s["speaker"]))
    return {
        "audio_id": f"primock57-d{dia}c{consulta:02d}",
        "patient_id": f"primock57-paciente-d{dia}c{consulta:02d}",
        "recorded_at": f"2022-01-0{dia}T09:00:00Z",
        "is_synthetic": True,
        "language": "en",
        "source": f"https://github.com/{REPO} ({base})",
        "segments": segmentos,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", type=Path, default=Path("data/primock57"))
    parser.add_argument("--audio", action="store_true", help="baixa também os .wav (centenas de MB)")
    args = parser.parse_args(argv)

    raw = args.dest / "raw"
    textgrids = [n for n in _listar("transcripts") if _NOME.fullmatch(n)]
    notas = [n for n in _listar("notes") if n.endswith(".json")]
    print(f"{len(textgrids)} TextGrid(s) e {len(notas)} nota(s) em {REPO}")
    for nome in textgrids:
        _baixar(f"{RAW}/transcripts/{nome}", raw / "transcripts" / nome)
    for nome in notas:
        _baixar(f"{RAW}/notes/{nome}", raw / "notes" / nome)
    if args.audio:
        wavs = [n for n in _listar("audio") if n.endswith(".wav")]
        for i, nome in enumerate(wavs, 1):
            print(f"  áudio {i}/{len(wavs)}: {nome}")
            _baixar(f"{MEDIA}/audio/{nome}", raw / "audio" / nome)

    consultas = sorted({(int(m[1]), int(m[2])) for m in map(_NOME.fullmatch, textgrids)})
    saida = args.dest / "transcripts"
    saida.mkdir(parents=True, exist_ok=True)
    for dia, consulta in consultas:
        dados = converter(raw, dia, consulta)
        (saida / f"{dados['audio_id']}.json").write_text(
            json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(consultas)} consulta(s) convertida(s) em {saida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
