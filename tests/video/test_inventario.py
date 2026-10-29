"""Testes do inventário de vídeos (``video.inventario``)."""

from __future__ import annotations

import shutil
from pathlib import Path

from video.inventario import (
    inventariar,
    inventariar_video,
    ler_inventario,
    resumo_inventario,
    salvar_inventario,
)
from video.leitor_video import criar_video_teste


def _pasta_com_videos(tmp_path: Path) -> Path:
    pasta = tmp_path / "videos"
    pasta.mkdir()
    criar_video_teste(pasta / "B_D_0001.mp4", quadros=10, fps=10.0)
    criar_video_teste(pasta / "B_N_100.mp4", quadros=20, fps=10.0)
    (pasta / "quebrado.mp4").write_bytes(b"nao e video")
    return pasta


def test_inventaria_todos_os_arquivos_incluindo_ilegivel(tmp_path: Path):
    itens = inventariar(_pasta_com_videos(tmp_path))
    assert len(itens) == 3
    por_nome = {i.nome: i for i in itens}
    assert por_nome["B_D_0001.mp4"].estado == "ok"
    assert por_nome["B_D_0001.mp4"].largura == 320
    assert por_nome["quebrado.mp4"].estado == "erro"
    assert por_nome["quebrado.mp4"].erro is not None


def test_duracao_e_metadados_derivados(tmp_path: Path):
    item = inventariar_video(_pasta_com_videos(tmp_path) / "B_N_100.mp4")
    assert item.total_frames == 20
    assert item.duracao_s == 2.0
    assert item.resolucao == "320x240"
    assert item.sha256


def test_duplicata_exata_detectada(tmp_path: Path):
    pasta = _pasta_com_videos(tmp_path)
    shutil.copy2(pasta / "B_D_0001.mp4", pasta / "B_D_0001_resized.mp4")
    itens = inventariar(pasta)
    resized = next(i for i in itens if i.nome == "B_D_0001_resized.mp4")
    assert resized.duplicata_de == "B_D_0001.mp4"
    resumo = resumo_inventario(itens)
    assert resumo["duplicatas_exatas"] == 1


def test_resumo_conta_por_cenario_e_estado(tmp_path: Path):
    itens = inventariar(_pasta_com_videos(tmp_path))
    resumo = resumo_inventario(itens)
    assert resumo["total_videos"] == 3
    assert resumo["videos_com_erro"] == 1
    assert resumo["por_cenario"]["cama"] == 2
    assert resumo["por_cenario"]["desconhecido"] == 1


def test_salvar_e_reler_inventario(tmp_path: Path):
    itens = inventariar(_pasta_com_videos(tmp_path))
    caminho_json = tmp_path / "inventario.json"
    caminho_csv = tmp_path / "inventario.csv"
    escritos = salvar_inventario(itens, caminho_csv=caminho_csv, caminho_json=caminho_json)
    assert Path(escritos["csv"]).is_file()
    assert Path(escritos["json"]).is_file()
    relidos = ler_inventario(caminho_json)
    assert {i.nome for i in relidos} == {i.nome for i in itens}
    assert {i.sha256 for i in relidos} == {i.sha256 for i in itens}
