"""Testes da extração de frames (``video.extracao_frames``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from video.extracao_frames import (
    ConfiguracaoExtracao,
    ManifestoFrames,
    extrair_frames,
    extrair_lote,
    video_id_de,
)
from video.inventario import inventariar
from video.leitor_video import criar_video_teste


def _videos(tmp_path: Path) -> Path:
    pasta = tmp_path / "videos"
    pasta.mkdir()
    criar_video_teste(pasta / "B_D_0001.mp4", quadros=20, fps=10.0)
    criar_video_teste(pasta / "B_N_100.mp4", quadros=20, fps=10.0)
    return pasta


def test_extrai_por_intervalo_e_registra_timestamp(tmp_path: Path):
    pasta = _videos(tmp_path)
    destino = tmp_path / "dataset_us07"
    config = ConfiguracaoExtracao(
        intervalo_s=0.5, max_frames=None, detectar_semelhantes=False, retomar=True
    )
    frames = extrair_frames(
        pasta / "B_D_0001.mp4", destino_raiz=destino, config=config
    )
    unicos = [f for f in frames if not f.duplicado]
    assert len(unicos) == 4
    assert [round(f.timestamp_s, 1) for f in unicos] == [0.0, 0.5, 1.0, 1.5]
    for frame in unicos:
        assert frame.arquivo_imagem is not None
        assert (destino / frame.arquivo_imagem).is_file()
        assert frame.sha256


def test_max_frames_limita(tmp_path: Path):
    pasta = _videos(tmp_path)
    config = ConfiguracaoExtracao(
        intervalo_s=0.1, max_frames=3, detectar_semelhantes=False, retomar=True
    )
    frames = extrair_frames(
        pasta / "B_D_0001.mp4", destino_raiz=tmp_path / "ds", config=config
    )
    assert len([f for f in frames if not f.duplicado]) == 3


def test_deduplicacao_de_frames_semelhantes(tmp_path: Path):
    pasta = _videos(tmp_path)
    config = ConfiguracaoExtracao(
        intervalo_s=0.1, max_frames=None, detectar_semelhantes=True, distancia_semelhanca=0
    )
    frames = extrair_frames(
        pasta / "B_D_0001.mp4", destino_raiz=tmp_path / "ds", config=config
    )
    assert any(f.duplicado for f in frames)


def test_min_frames_por_video_promove_descartes(tmp_path: Path):
    pasta = _videos(tmp_path)
    base = ConfiguracaoExtracao(
        intervalo_s=0.1, max_frames=None, detectar_semelhantes=True, distancia_semelhanca=0
    )
    sem_minimo = extrair_frames(
        pasta / "B_D_0001.mp4", destino_raiz=tmp_path / "sem", config=base
    )
    assert sum(not f.duplicado for f in sem_minimo) < 4

    config = ConfiguracaoExtracao(
        intervalo_s=0.1,
        max_frames=None,
        detectar_semelhantes=True,
        distancia_semelhanca=0,
        min_frames_por_video=4,
    )
    destino = tmp_path / "com"
    frames = extrair_frames(pasta / "B_D_0001.mp4", destino_raiz=destino, config=config)
    unicos = [f for f in frames if not f.duplicado]
    assert len(unicos) == 4
    for frame in unicos:
        assert frame.arquivo_imagem is not None
        assert (destino / frame.arquivo_imagem).is_file()
        assert frame.sha256


def test_min_frames_por_video_valida_limites(tmp_path: Path):
    with pytest.raises(ValueError):
        ConfiguracaoExtracao(min_frames_por_video=0)
    with pytest.raises(ValueError):
        ConfiguracaoExtracao(max_frames=2, min_frames_por_video=3)


def test_manifesto_permite_retomada(tmp_path: Path):
    pasta = _videos(tmp_path)
    destino = tmp_path / "ds"
    itens = inventariar(pasta)
    config = ConfiguracaoExtracao(intervalo_s=0.5, detectar_semelhantes=False)
    manifesto = ManifestoFrames(destino / "metadata" / "frames_manifest.jsonl")
    extrair_lote(itens, raiz_videos=pasta, destino_raiz=destino, config=config)
    recarregado = ManifestoFrames(destino / "metadata" / "frames_manifest.jsonl")
    assert len(recarregado) > 0
    id_primeiro = next(iter(recarregado.ids_do_video(video_id_de("B_D_0001.mp4"))))
    assert id_primeiro in recarregado
    # segunda rodada pula os vídeos já presentes
    relatorio = extrair_lote(itens, raiz_videos=pasta, destino_raiz=destino, config=config)
    assert relatorio.videos_pulados == len(itens)
    assert manifesto is not None


def test_video_id_sanitiza_nome(tmp_path: Path):
    assert video_id_de("B_D_0001.mp4") == "B_D_0001"
    assert video_id_de("video triste!.mp4") == "video_triste_"
