import os
from pathlib import Path
from dotenv import load_dotenv
import sys
import subprocess

load_dotenv()

DRIVE_FOLDER_URL = os.getenv("GOOGLE_DRIVE_FOLDER_URL")

if not DRIVE_FOLDER_URL:
    raise RuntimeError(
        "A variável GOOGLE_DRIVE_FOLDER_URL não foi definida no arquivo .env."
    )

PROJECT_ROOT = Path.cwd()
CONTENT_DIR = PROJECT_ROOT


def download_contents() -> None:
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    print()
    print("=" * 70)
    print(" DOWNLOAD DOS CONTEÚDOS")
    print("=" * 70)
    print()
    print(f"Origem : {DRIVE_FOLDER_URL}")
    print(f"Destino: {CONTENT_DIR}")
    print()

    command = [
        sys.executable,
        "-m",
        "gdown",
        DRIVE_FOLDER_URL,
        "--folder",
        "--output",
        str(CONTENT_DIR),
    ]

    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as error:
        print(f"O gdown terminou com código: {error.returncode}")

        sys.exit(error.returncode)
    except KeyboardInterrupt:
        print("Download interrompido pelo usuário.")
        sys.exit(130)

    print()
    print("=" * 70)
    print(" DOWNLOAD CONCLUÍDO")
    print("=" * 70)
    print()
    print(f"Os conteúdos estão disponíveis em:")
    print(f" {CONTENT_DIR}")
    print()


def main() -> None:
    download_contents()


if __name__ == "__main__":
    main()
