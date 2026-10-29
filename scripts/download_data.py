"""Baixa as tabelas do eICU-CRD Demo (PhysioNet, acesso aberto) usadas na US15.

    python scripts/download_data.py              # baixa o que falta
    python scripts/download_data.py --force      # baixa de novo
    python scripts/download_data.py --dest outra/pasta

Os arquivos vão para ``sources.eicu.data_dir`` do config (``data/`` não é
versionado). Cada arquivo é conferido contra o ``SHA256SUMS.txt`` publicado
pelo PhysioNet; um arquivo com hash diferente é apagado e o script falha.

Licença dos dados: Open Data Commons Open Database License v1.0 (ODbL).
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import sys
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from vitals_anomaly.config import load_config

CHECKSUMS_FILE = "SHA256SUMS.txt"
CHUNK = 1 << 20


def _baixar(url: str, destino: Path, tentativas: int) -> None:
    """Baixa para ``<destino>.part`` e renomeia no fim.

    O servidor do PhysioNet às vezes derruba a conexão no meio; cada nova
    tentativa retoma do byte onde parou (cabeçalho HTTP Range).
    """
    parcial = destino.with_suffix(destino.suffix + ".part")
    for tentativa in range(1, tentativas + 1):
        inicio = parcial.stat().st_size if parcial.exists() else 0
        req = urllib.request.Request(url, headers={"Range": f"bytes={inicio}-"} if inicio else {})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                # 206 = o servidor aceitou retomar; 200 = mandou o arquivo inteiro.
                retomou = inicio and resp.status == 206
                total = (inicio if retomou else 0) + int(resp.headers.get("Content-Length", -1))
                with parcial.open("ab" if retomou else "wb") as fh:
                    while bloco := resp.read(CHUNK):
                        fh.write(bloco)
            # Se a conexão cai no meio, o urllib só devolve fim de arquivo, sem
            # erro: o tamanho é a única forma de perceber o corte.
            recebido = parcial.stat().st_size
            if total >= 0 and recebido < total:
                raise http.client.IncompleteRead(b"", total - recebido)
            parcial.replace(destino)
            return
        except (OSError, http.client.HTTPException) as exc:
            print(f"  tentativa {tentativa}/{tentativas} interrompida ({exc!r}); retomando...", file=sys.stderr)
    raise OSError(f"download de {url} falhou após {tentativas} tentativas")


def _sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as fh:
        while bloco := fh.read(CHUNK):
            h.update(bloco)
    return h.hexdigest()


def _checksums(base_url: str) -> dict[str, str]:
    with urllib.request.urlopen(base_url + CHECKSUMS_FILE, timeout=60) as resp:
        linhas = resp.read().decode("utf-8").splitlines()
    return {nome: digest for digest, nome in (linha.split(maxsplit=1) for linha in linhas if linha.strip())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=None, help="config YAML (padrão: o do pacote)")
    parser.add_argument("--dest", type=Path, default=None, help="pasta de destino (padrão: sources.eicu.data_dir)")
    parser.add_argument("--force", action="store_true", help="baixa mesmo se o arquivo já existir")
    parser.add_argument("--retries", type=int, default=10, help="tentativas por arquivo, retomando de onde parou")
    args = parser.parse_args(argv)

    cfg = load_config(args.config).eicu
    destino = args.dest or cfg.data_dir
    destino.mkdir(parents=True, exist_ok=True)
    base_url = cfg.base_url if cfg.base_url.endswith("/") else cfg.base_url + "/"
    esperados = _checksums(base_url)

    for nome in (cfg.files.periodic, cfg.files.aperiodic, cfg.files.patient):
        caminho = destino / nome
        if nome not in esperados:
            print(f"{nome}: ausente em {CHECKSUMS_FILE} do PhysioNet", file=sys.stderr)
            return 1
        if caminho.exists() and not args.force and _sha256(caminho) == esperados[nome]:
            print(f"{nome}: já existe, hash confere")
            continue
        print(f"{nome}: baixando de {base_url + nome} ...")
        try:
            _baixar(base_url + nome, caminho, args.retries)
        except OSError as exc:
            print(f"{nome}: {exc}. Rode de novo para continuar de onde parou.", file=sys.stderr)
            return 1
        if _sha256(caminho) != esperados[nome]:
            caminho.unlink()
            print(f"{nome}: SHA-256 não confere com o PhysioNet; arquivo removido", file=sys.stderr)
            return 1
        print(f"{nome}: ok ({caminho.stat().st_size / 1e6:.1f} MB)")

    print(f"Dados em {destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
