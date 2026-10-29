"""Desenho das detecções e ROIs — US07 (opcional).

Nada aqui altera a lógica: o pipeline decide os eventos e depois chama
:func:`anotar` só quando a visualização está ligada. Se o OpenCV falhar ao
gravar, o processamento continua e a falha é registrada no log.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType

import cv2
import numpy as np

from .areas import ConjuntoAreas
from .modelos import Deteccao, MetadadosVideo
from .monitor import EventoAreaCritica, Transicao

log = logging.getLogger(__name__)

COR_ROI = (0, 200, 255)
COR_ROI_ENTRADA = (0, 255, 80)
COR_CAIXA = (60, 220, 60)
COR_ENTRADA = (0, 255, 80)
COR_SAIDA = (60, 60, 255)
ESPESSURA_LINHA = 2
FONTE = cv2.FONT_HERSHEY_SIMPLEX
ESCALA_FONTE = 0.5


class GravadorVideo:
    """Escritor de vídeo anotado, com o FPS do arquivo original.

    Se o contêiner não aceitar o codec, avisa e desliga em vez de derrubar o
    pipeline: a visualização é acessória.
    """

    def __init__(
        self, caminho: str | Path, metadados: MetadadosVideo
    ) -> None:
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.metadados = metadados
        self.ativo = False
        self._escritor: cv2.VideoWriter | None = None

    def abrir(self) -> GravadorVideo:
        codec = "mp4v" if self.caminho.suffix.lower() == ".mp4" else "MJPG"
        escritor = cv2.VideoWriter(
            str(self.caminho),
            cv2.VideoWriter_fourcc(*codec),
            max(self.metadados.fps, 1.0),
            (self.metadados.largura, self.metadados.altura),
        )
        if not escritor.isOpened():
            escritor.release()
            log.warning(
                "não foi possível abrir o gravador em %s (codec %s); "
                "anotação de vídeo desativada",
                self.caminho,
                codec,
            )
            return self
        self._escritor = escritor
        self.ativo = True
        return self

    def escrever(self, imagem: np.ndarray) -> None:
        if self._escritor is None:
            return
        try:
            self._escritor.write(imagem)
        except (cv2.error, OSError) as erro:  # pragma: no cover — falha de I/O
            log.warning("falha ao escrever quadro em %s: %s", self.caminho, erro)
            self.fechar()

    def fechar(self) -> None:
        if self._escritor is not None:
            self._escritor.release()
            self._escritor = None
        self.ativo = False

    def __enter__(self) -> GravadorVideo:
        return self.abrir()

    def __exit__(
        self,
        tipo_exc: type[BaseException] | None,
        valor: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.fechar()


def desenhar_rois(imagem: np.ndarray, areas: ConjuntoAreas) -> np.ndarray:
    """Desenha o polígono e o nome de cada área crítica."""
    for area in areas:
        pontos = np.array(area.pontos, dtype=np.int32).reshape(-1, 1, 2)
        cv2.polylines(imagem, [pontos], True, COR_ROI, ESPESSURA_LINHA)
        cv2.putText(
            imagem,
            area.nome,
            (int(area.pontos[0][0]), max(int(area.pontos[0][1]) - 6, 12)),
            FONTE,
            ESCALA_FONTE,
            COR_ROI,
            1,
            cv2.LINE_AA,
        )
    return imagem


def desenhar_deteccoes(
    imagem: np.ndarray, deteccoes: Sequence[Deteccao], *, mostrar_labels: bool = True
) -> np.ndarray:
    """Desenha bounding boxes, nome da classe e confiança."""
    for deteccao in deteccoes:
        x1, y1, x2, y2 = (int(v) for v in deteccao.caixa.como_lista())
        cv2.rectangle(imagem, (x1, y1), (x2, y2), COR_CAIXA, ESPESSURA_LINHA)
        if not mostrar_labels:
            continue
        rotulo = f"{deteccao.class_name} {deteccao.confianca:.2f}"
        if deteccao.track_id is not None:
            rotulo = f"#{deteccao.track_id} {rotulo}"
        _rotulo_com_fundo(imagem, rotulo, (x1, y1), COR_CAIXA)
    return imagem


def desenhar_transicoes(
    imagem: np.ndarray, transicoes: Sequence[EventoAreaCritica]
) -> np.ndarray:
    """Destaque a seta/texto da entrada ou saída no quadro em que ocorreu."""
    for transicao in transicoes:
        deteccao = transicao.deteccao
        centro = deteccao.caixa.centro
        cor = COR_ENTRADA if transicao.transicao is Transicao.ENTRADA else COR_SAIDA
        rotulo = (
            f"{transicao.transicao.name} · {transicao.nome_area}"
            f" · {deteccao.tempo_s:.2f}s"
        )
        cv2.circle(imagem, (int(centro[0]), int(centro[1])), 14, cor, 3)
        _rotulo_com_fundo(imagem, rotulo, (int(centro[0]), int(centro[1]) - 22), cor)
    return imagem


def _rotulo_com_fundo(
    imagem: np.ndarray, texto: str, origem: tuple[int, int], cor: tuple[int, int, int]
) -> None:
    """Escreve o texto com retângulo de fundo, para ficar legível em vídeo escuro."""
    (largura, altura), _ = cv2.getTextSize(texto, FONTE, ESCALA_FONTE, 1)
    x, y = origem
    y = max(y, altura + 4)
    fundo = imagem.copy()
    cv2.rectangle(
        fundo,
        (x, y - altura - 4),
        (x + largura + 4, y + 4),
        cor,
        cv2.FILLED,
    )
    cv2.addWeighted(fundo, 0.35, imagem, 0.65, 0, imagem)
    cv2.putText(
        imagem, texto, (x + 2, y), FONTE, ESCALA_FONTE, (0, 0, 0), 1, cv2.LINE_AA
    )


def anotar(
    imagem: np.ndarray,
    *,
    areas: ConjuntoAreas,
    deteccoes: Sequence[Deteccao] = (),
    transicoes: Sequence[EventoAreaCritica] = (),
    mostrar_rois: bool = True,
    mostrar_labels: bool = True,
    carimbo: str = "",
) -> np.ndarray:
    """Devolve uma cópia anotada do frame.

    A cópia evita que o desenho contamine o frame usado pela inferência em um
    laço reprocessando o mesmo buffer.
    """
    saida = imagem.copy()
    if mostrar_rois:
        desenhar_rois(saida, areas)
    desenhar_deteccoes(saida, deteccoes, mostrar_labels=mostrar_labels)
    desenhar_transicoes(saida, transicoes)
    if carimbo:
        cv2.putText(
            saida,
            carimbo,
            (10, max(saida.shape[0] - 10, 18)),
            FONTE,
            ESCALA_FONTE,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    return saida


__all__ = [
    "GravadorVideo",
    "anotar",
    "desenhar_deteccoes",
    "desenhar_rois",
    "desenhar_transicoes",
]
