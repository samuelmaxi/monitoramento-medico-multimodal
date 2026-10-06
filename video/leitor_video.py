"""Leitura de vídeo com OpenCV — US07.

Responsabilidades:

- abrir o arquivo e expor metadados (fps, resolução, total de quadros);
- entregar quadros um a um, com índice e instante em segundos;
- tratar os casos de falha que a US07 exige: arquivo inexistente, vídeo que o
  OpenCV não abre (codec/formato), vídeo sem quadros, FPS ausente ou inválido e
  fim de vídeo.

O instante do quadro é ``indice / fps``. Com FPS inválido, o leitor assume
``FPS_PADRAO`` (25, valor típico de captura clínica) e registra um aviso: sem
FPS real não há como converter índice em tempo, e inventar um valor silencioso
produziria timestamps errados no evento.

O leitor é um context manager, então ``cap.release()`` acontece mesmo com
exceção no meio do processamento::

    with LeitorVideo("conteudos/videos/fisioterapia.mp4") as leitor:
        for quadro in leitor:
            ...
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import cv2
import numpy as np

from .modelos import MetadadosVideo, Quadro

log = logging.getLogger(__name__)

FPS_PADRAO = 25.0
"""FPS assumido quando o vídeo não informa um valor utilizável."""


class ErroDeVideo(RuntimeError):
    """Falha ao abrir ou ler o vídeo."""


class VideoNaoEncontrado(ErroDeVideo):
    """O caminho não existe ou não é um arquivo."""


class VideoNaoAberto(ErroDeVideo):
    """O OpenCV não conseguiu abrir o arquivo (codec, container ou permissão)."""


class VideoSemQuadros(ErroDeVideo):
    """O vídeo abriu, mas não devolveu nenhum frame."""


@dataclass(frozen=True, slots=True)
class MetadadosLeitura:
    """Metadados exatamente como lidos do contêiner, antes de qualquer ajuste."""

    fps_bruto: float
    largura: int
    altura: int
    total_quadros: int | None

    @property
    def fps_valido(self) -> bool:
        return self.fps_bruto > 0 and np.isfinite(self.fps_bruto)


class LeitorVideo:
    """Iterador de quadros de um arquivo de vídeo.

    Args:
        caminho: caminho do arquivo de vídeo.
        fps_padrao: valor usado quando o contêiner não tem FPS válido.
        max_quadros: limite opcional de quadros lidos (útil em testes e na
            validação rápida do pipeline).

    Raises:
        VideoNaoEncontrado: o caminho não existe.
        VideoNaoAberto: o OpenCV não abriu o arquivo.
        VideoSemQuadros: nenhum frame pôde ser lido na abertura.
    """

    def __init__(
        self,
        caminho: str | Path,
        *,
        fps_padrao: float = FPS_PADRAO,
        max_quadros: int | None = None,
    ) -> None:
        self.caminho = Path(caminho)
        self._fps_padrao = fps_padrao
        self._max_quadros = max_quadros
        self._capture: cv2.VideoCapture | None = None
        self._metadados: MetadadosVideo | None = None
        self._indice = -1
        self._fps_assumido = False

    @property
    def caminho_video(self) -> Path:
        return self.caminho

    @property
    def metadados(self) -> MetadadosVideo:
        """Metadados do vídeo. Só disponível após o ``with``/``abrir()``."""
        if self._metadados is None:
            raise ErroDeVideo("vídeo ainda não foi aberto; use abrir() ou 'with'")
        return self._metadados

    @property
    def fps_foi_assumido(self) -> bool:
        """``True`` quando o contêiner não tinha FPS válido e o padrão foi usado."""
        return self._fps_assumido

    def abrir(self) -> LeitorVideo:
        """Abre o arquivo e lê os metadados. Idempotente."""
        if self._capture is not None:
            return self

        if not self.caminho.is_file():
            raise VideoNaoEncontrado(f"vídeo não encontrado: {self.caminho}")

        capture = cv2.VideoCapture(str(self.caminho))
        if not capture.isOpened():
            capture.release()
            raise VideoNaoAberto(
                f"OpenCV não conseguiu abrir {self.caminho}; "
                "verifique o contêiner/codec ou converta com ffmpeg"
            )

        bruto = self._ler_metadados_brutos(capture)
        fps = bruto.fps_bruto if bruto.fps_valido else self._fps_padrao
        self._fps_assumido = not bruto.fps_valido
        if self._fps_assumido:
            log.warning(
                "vídeo %s sem FPS válido (bruto=%s); usando %.1f. "
                "Os timestamps do evento serão estimados.",
                self.caminho,
                bruto.fps_bruto,
                fps,
            )

        self._capture = capture
        self._metadados = MetadadosVideo(
            fps=fps,
            largura=bruto.largura,
            altura=bruto.altura,
            total_quadros=bruto.total_quadros,
            origem=str(self.caminho),
        )
        self._indice = -1
        return self

    def quadros(self) -> Iterator[Quadro]:
        """Itera os quadros do vídeo, em ordem.

        Yields:
            :class:`~video.modelos.Quadro` com índice 0-based e ``tempo_s``.

        Raises:
            VideoSemQuadros: se o primeiro ``read()`` falhar, o que indica
                arquivo vazio ou truncado.
        """
        if self._capture is None:
            self.abrir()
        assert self._capture is not None  # garantido por abrir()
        assert self._metadados is not None

        lidos = 0
        while self._max_quadros is None or lidos < self._max_quadros:
            ok, imagem = self._capture.read()
            if not ok or imagem is None:
                break
            self._indice += 1
            lidos += 1
            yield Quadro(
                indice=self._indice,
                imagem=imagem,
                tempo_s=self._indice / self._metadados.fps if self._metadados.fps else 0.0,
            )

        if lidos == 0 and self._indice == -1:
            raise VideoSemQuadros(
                f"vídeo {self.caminho} não devolveu nenhum frame "
                f"(fps={self._metadados.fps}, resolução={self._metadados.resolucao})"
            )

    def __iter__(self) -> Iterator[Quadro]:
        return self.quadros()

    def liberar(self) -> None:
        """Libera o contêiner do OpenCV."""
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def __enter__(self) -> LeitorVideo:
        return self.abrir()

    def __exit__(
        self,
        tipo_exc: type[BaseException] | None,
        valor: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.liberar()

    def frame_para_timestamp(
        self, indice_quadro: int, *, inicio: float = 0.0
    ) -> float:
        """Instante em segundos de um quadro: ``indice / fps + inicio``.

        Args:
            indice_quadro: índice 0-based do quadro.
            inicio: deslocamento em segundos do início da gravação (offset de
                câmera). Para vídeo arquivo, ``0.0``.
        """
        fps = self.metadados.fps or self._fps_padrao
        return indice_quadro / fps + inicio

    @staticmethod
    def _ler_metadados_brutos(capture: cv2.VideoCapture) -> MetadadosLeitura:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        largura = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        altura = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        return MetadadosLeitura(
            fps_bruto=fps,
            largura=largura,
            altura=altura,
            total_quadros=total if total > 0 else None,
        )


def criar_video_teste(
    caminho: str | Path,
    *,
    quadros: int = 12,
    largura: int = 320,
    altura: int = 240,
    fps: float = 10.0,
    cor: tuple[int, int, int] = (30, 30, 30),
) -> Path:
    """Grava um vídeo sintético em disco, para testes e para testes de fumaça.

    Não usa nenhum asset externo: o arquivo gerado tem quadros contáveis e conhecidos,
    o que torna o teste de vídeo determinístico.

    Args:
        caminho: arquivo ``.avi``/``.mp4`` a criar.
        quadros: número de frames.
        largura: largura em pixels.
        altura: altura em pixels.
        fps: FPS gravado no contêiner.
        cor: cor de fundo BGR.

    Returns:
        O caminho gravado.
    """
    destino = Path(caminho)
    destino.parent.mkdir(parents=True, exist_ok=True)
    quatrocc = cv2.VideoWriter_fourcc(*"MJPG")
    writer = cv2.VideoWriter(str(destino), quatrocc, fps, (largura, altura))
    if not writer.isOpened():
        raise ErroDeVideo(f"não foi possível criar vídeo de teste em {destino}")
    try:
        for indice in range(quadros):
            imagem = np.full((altura, largura, 3), cor, dtype=np.uint8)
            cv2.putText(
                imagem,
                f"f{indice}",
                (10, altura // 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (255, 255, 255),
                2,
            )
            writer.write(imagem)
    finally:
        writer.release()
    return destino


__all__ = [
    "FPS_PADRAO",
    "ErroDeVideo",
    "LeitorVideo",
    "MetadadosLeitura",
    "VideoNaoAberto",
    "VideoNaoEncontrado",
    "VideoSemQuadros",
    "criar_video_teste",
]
