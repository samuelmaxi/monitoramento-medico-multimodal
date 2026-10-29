"""Leitor OpenCV: abertura, frames, fim e FPS degradado."""

from __future__ import annotations

from pathlib import Path

import pytest

from video import LeitorVideo, VideoNaoAberto, VideoNaoEncontrado, VideoSemQuadros
from video.leitor_video import MetadadosLeitura


def test_ler_video_sintetico_entrega_indices_fps_e_resolucao(leitor):
    quadros = list(leitor.quadros())
    assert len(quadros) == 20
    assert quadros[0].indice == 0
    assert quadros[1].tempo_s == pytest.approx(0.1)
    assert quadros[0].imagem.shape[:2] == (240, 320)
    assert leitor.metadados.fps == pytest.approx(10.0)
    assert leitor.metadados.resolucao == (320, 240)


def test_caminho_inexistente_gera_erro(tmp_path: Path):
    with pytest.raises(VideoNaoEncontrado), LeitorVideo(tmp_path / "nao-existe.mp4"):
        pass


def test_arquivo_invalido_gera_erro_abertura(tmp_path: Path):
    arquivo = tmp_path / "inválido.mp4"
    arquivo.write_bytes("não é vídeo".encode())
    with pytest.raises(VideoNaoAberto), LeitorVideo(arquivo):
        pass


def test_fim_do_video_nao_repete_frames(video_sintetico: Path):
    with LeitorVideo(video_sintetico) as leitor:
        assert len(list(leitor.quadros())) == 20
        assert list(leitor.quadros()) == []


def test_fps_invalido_usa_fallback_e_avisa(video_sintetico: Path, caplog):
    leitor = LeitorVideo(video_sintetico, fps_padrao=25.0)
    leitor._ler_metadados_brutos = lambda _capture: MetadadosLeitura(0.0, 320, 240, 1)
    with leitor:
        assert leitor.metadados.fps == 25.0
        assert leitor.fps_foi_assumido
        assert next(leitor.quadros()).tempo_s == 0.0
    assert "sem FPS válido" in caplog.text


def test_video_sem_quadros_gera_erro_no_primeiro_read(video_sintetico: Path, monkeypatch):
    import video.leitor_video as modulo

    class CaptureVazio:
        def isOpened(self) -> bool:
            return True

        def get(self, propriedade: int) -> float:
            valores = {
                modulo.cv2.CAP_PROP_FPS: 10.0,
                modulo.cv2.CAP_PROP_FRAME_WIDTH: 320,
                modulo.cv2.CAP_PROP_FRAME_HEIGHT: 240,
                modulo.cv2.CAP_PROP_FRAME_COUNT: 0,
            }
            return valores[propriedade]

        def read(self) -> tuple[bool, None]:
            return False, None

        def release(self) -> None:
            pass

    monkeypatch.setattr(modulo.cv2, "VideoCapture", lambda _path: CaptureVazio())
    with LeitorVideo(video_sintetico) as leitor, pytest.raises(VideoSemQuadros):
        list(leitor.quadros())
