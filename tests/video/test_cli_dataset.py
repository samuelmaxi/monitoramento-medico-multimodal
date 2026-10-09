"""Testes de fumaça da CLI ``scripts/dataset_us07.py``."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from video.leitor_video import criar_video_teste

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "dataset_us07.py"


def _carregar_modulo():
    spec = importlib.util.spec_from_file_location("dataset_us07_cli", _SCRIPT)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture()
def cli():
    return _carregar_modulo()


@pytest.fixture()
def videos(tmp_path: Path) -> Path:
    pasta = tmp_path / "videos"
    cores = [(10, 10, 10), (60, 60, 60), (120, 120, 120), (200, 200, 200)]
    for i, cor in enumerate(cores):
        criar_video_teste(pasta / f"B_D_{i:04d}.mp4", quadros=20, fps=10.0, cor=cor)
    return pasta


def test_cli_inventariar_extrair_montar(cli, tmp_path: Path, videos: Path):
    ds = tmp_path / "ds"
    assert cli.main(["--destino", str(ds), "inventariar", "--videos", str(videos)]) == 0
    assert (ds / "metadata" / "inventario.json").is_file()

    codigo = cli.main(
        [
            "--destino", str(ds), "extrair", "--videos", str(videos),
            "--intervalo", "1.0", "--max-frames", "2", "--sem-dedup",
        ]
    )
    assert codigo == 0
    assert (ds / "metadata" / "frames_manifest.jsonl").is_file()

    assert cli.main(["--destino", str(ds), "montar"]) == 0
    assert (ds / "data.yaml").is_file()
    from video.dataset import verificar_vazamento

    assert verificar_vazamento(ds) == []


def test_cli_validar_sem_labels_retorna_erro(cli, tmp_path: Path, videos: Path):
    ds = tmp_path / "ds"
    cli.main(["--destino", str(ds), "inventariar", "--videos", str(videos)])
    cli.main(
        ["--destino", str(ds), "extrair", "--videos", str(videos), "--sem-dedup"]
    )
    cli.main(["--destino", str(ds), "montar"])
    assert cli.main(["--destino", str(ds), "validar"]) == 1


def test_cli_treinar_recusa_sem_aptidao(cli, tmp_path: Path, videos: Path):
    ds = tmp_path / "ds"
    cli.main(["--destino", str(ds), "inventariar", "--videos", str(videos)])
    cli.main(["--destino", str(ds), "extrair", "--videos", str(videos), "--sem-dedup"])
    cli.main(["--destino", str(ds), "montar"])
    assert cli.main(["--destino", str(ds), "treinar"]) == 2


def test_cli_gerar_data_yaml(cli, tmp_path: Path):
    ds = tmp_path / "ds"
    assert cli.main(["--destino", str(ds), "gerar-data-yaml"]) == 0
    assert (ds / "data.yaml").is_file()
