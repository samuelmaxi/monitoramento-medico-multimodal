"""Baixa o YAMNet em ONNX e o mapa de classes do AudioSet para ``models/audio/`` (fora do Git).

    python scripts/baixar_modelos_audio.py [--destino models/audio] [--forcar]

Usado pelos eventos respiratórios da US12 (``audio.vocal.respiratorio``). Sem os arquivos, a análise
vocal roda normalmente, só que sem o critério de eventos respiratórios.

Cada arquivo tem o SHA-256 fixado: a URL do Hugging Face aponta para a ``main`` (mutável), então é o
hash que garante que o arquivo é o validado. Se o conteúdo mudar, o download é recusado.

Origem e licença:

- ``yamnet.onnx``: conversão ``tf2onnx`` do YAMNet do Google, espelho no Hugging Face
  (``andrelgomes/yamnet-onnx``), Apache-2.0. Entrada ``waveform`` float32 (16 kHz, mono) e saída
  ``output_0`` com 521 scores por quadro. Se o espelho sair do ar, converta o modelo do TF Hub com
  ``tf2onnx`` e coloque o arquivo em ``models/audio/yamnet.onnx`` (a ordem das 521 classes é
  a do mapa).
- ``yamnet_class_map.csv``: mapa de classes do repositório ``tensorflow/models``, Apache-2.0.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

DESTINO_PADRAO = Path("models/audio")
TIMEOUT_S = 60


@dataclass(frozen=True)
class Arquivo:
    nome: str
    url: str
    sha256: str
    bytes_esperados: int | None = None


ARQUIVOS = (
    Arquivo(
        "yamnet.onnx",
        "https://huggingface.co/andrelgomes/yamnet-onnx/resolve/main/yamnet.onnx",
        "1510041dce24a2e9e84ec546807ac408ae496da6d1ed41bc3ccba649623f8e19",
        16_093_603,
    ),
    Arquivo(
        "yamnet_class_map.csv",
        "https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/"
        "yamnet_class_map.csv",
        "cdf24d193e196d9e95912a2667051ae203e92a2ba09449218ccb40ef787c6df2",
        14_096,
    ),
)

Abrir = Callable[..., object]


class ErroDeDownload(RuntimeError):
    pass


def sha256_de(caminho: Path) -> str:
    resumo = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            resumo.update(bloco)
    return resumo.hexdigest()


def baixar(
    arquivo: Arquivo, destino: Path, *, forcar: bool = False, abrir: Abrir | None = None
) -> str:
    """Baixa um arquivo e confere o SHA-256. Devolve ``"ja_existia"`` ou ``"baixado"``.

    Grava em um temporário e só renomeia depois de conferir o hash: nunca fica um arquivo pela
    metade nem um com conteúdo diferente do esperado.
    """
    abrir = abrir or urllib.request.urlopen
    caminho = destino / arquivo.nome
    if caminho.is_file() and not forcar:
        if sha256_de(caminho) == arquivo.sha256:
            return "ja_existia"
        raise ErroDeDownload(
            f"{caminho} existe mas o SHA-256 não confere; apague-o ou use --forcar")
    destino.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(caminho.suffix + ".parte")
    resumo = hashlib.sha256()
    try:
        with abrir(arquivo.url, timeout=TIMEOUT_S) as resposta, temporario.open("wb") as saida:  # type: ignore[attr-defined]
            for bloco in iter(lambda: resposta.read(1024 * 1024), b""):  # type: ignore[attr-defined]
                resumo.update(bloco)
                saida.write(bloco)
    except (urllib.error.URLError, OSError, TimeoutError) as erro:
        temporario.unlink(missing_ok=True)
        raise ErroDeDownload(f"falha ao baixar {arquivo.url}: {erro}") from erro
    if resumo.hexdigest() != arquivo.sha256:
        temporario.unlink(missing_ok=True)
        raise ErroDeDownload(
            f"{arquivo.nome}: SHA-256 {resumo.hexdigest()} diferente do esperado "
            f"{arquivo.sha256}; o arquivo remoto mudou e não foi salvo")
    temporario.replace(caminho)
    return "baixado"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--destino", type=Path, default=DESTINO_PADRAO)
    parser.add_argument("--forcar", action="store_true", help="baixa de novo mesmo se já existir")
    args = parser.parse_args(argv)

    for arquivo in ARQUIVOS:
        try:
            situacao = baixar(arquivo, args.destino, forcar=args.forcar)
        except ErroDeDownload as erro:
            print(f"[ERRO] {erro}", file=sys.stderr)
            return 1
        print(f"[{situacao}] {args.destino / arquivo.nome}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
