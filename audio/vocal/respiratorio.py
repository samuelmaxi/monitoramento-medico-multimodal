"""Eventos respiratórios (tosse, respiração, sibilo, arfar) com YAMNet pré-treinado no AudioSet.

O YAMNet roda em ONNX (``onnxruntime``): dependência leve e sem TensorFlow. Modelo e mapa de
classes são baixados por ``scripts/baixar_modelos_audio.py`` para ``models/audio/`` (fora do Git).

O modelo recebe a forma de onda em 16 kHz (float32, mono) e devolve, a cada 0,48 s, um vetor de
521 scores (um por classe do AudioSet) para uma janela de 0,975 s. Um evento é um trecho contínuo
de quadros com o score da classe acima do mínimo, e leva o score máximo do trecho.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from contratos import Severidade

from .config import ErroDeConfiguracao, RespiratorioCfg

log = logging.getLogger("audio.vocal")

TAXA_HZ = 16000
QUADRO_S = 0.975
"""Duração da janela de um quadro de scores (15 600 amostras a 16 kHz)."""
PASSO_S = 0.48
"""Passo entre quadros (7 680 amostras)."""
N_CLASSES = 521
NOME_DO_MODELO = "yamnet-onnx"

Sessao = Callable[[np.ndarray], np.ndarray]
"""Função ``waveform float32 (n,) -> scores (quadros, 521)``; o teste injeta uma falsa."""


class ErroDeModelo(RuntimeError):
    """O arquivo do modelo ou o mapa de classes não está no formato esperado."""


@dataclass(frozen=True)
class EventoRespiratorio:
    tipo: str
    classe_audioset: str
    inicio_s: float
    fim_s: float
    score_max: float
    score_medio: float
    quadros: int
    severidade: Severidade

    def para_dict(self) -> dict[str, float | int | str]:
        return {
            "tipo": self.tipo,
            "classe_audioset": self.classe_audioset,
            "inicio_s": round(self.inicio_s, 3),
            "fim_s": round(self.fim_s, 3),
            "score_max": round(self.score_max, 4),
            "score_medio": round(self.score_medio, 4),
            "quadros": self.quadros,
            "severidade": self.severidade.value,
        }


@dataclass(frozen=True)
class ResumoRespiratorio:
    """Resultado da etapa respiratória. ``disponivel=False`` = o modelo não foi carregado."""

    disponivel: bool
    eventos: tuple[EventoRespiratorio, ...] = ()

    @property
    def score_max(self) -> float:
        return max((e.score_max for e in self.eventos), default=0.0)


def carregar_nomes_classes(caminho: str | Path) -> list[str]:
    """Nomes das 521 classes, na ordem dos índices, a partir do ``yamnet_class_map.csv``."""
    caminho = Path(caminho)
    if not caminho.is_file():
        raise ErroDeModelo(f"mapa de classes do YAMNet não encontrado: {caminho}")
    with caminho.open(encoding="utf-8", newline="") as arquivo:
        linhas = list(csv.DictReader(arquivo))
    try:
        por_indice = {int(linha["index"]): linha["display_name"] for linha in linhas}
    except (KeyError, ValueError) as erro:
        raise ErroDeModelo(f"{caminho.name}: esperado 'index,mid,display_name' ({erro})") from None
    if sorted(por_indice) != list(range(N_CLASSES)):
        raise ErroDeModelo(f"{caminho.name}: esperadas {N_CLASSES} classes de 0 a {N_CLASSES - 1}")
    return [por_indice[i] for i in range(N_CLASSES)]


class SessaoYamnetOnnx:
    """Envolve a ``InferenceSession``: entrada ``waveform`` e a saída de 521 colunas (scores)."""

    def __init__(self, caminho_modelo: str | Path) -> None:
        import onnxruntime as ort

        try:
            self._sessao = ort.InferenceSession(
                str(caminho_modelo), providers=["CPUExecutionProvider"])
        except Exception as erro:
            raise ErroDeModelo(
                f"não foi possível abrir o modelo {caminho_modelo}: {erro}") from erro
        entradas = self._sessao.get_inputs()
        saidas = [o for o in self._sessao.get_outputs() if o.shape and o.shape[-1] == N_CLASSES]
        if len(entradas) != 1 or len(saidas) != 1:
            raise ErroDeModelo(
                f"{Path(caminho_modelo).name}: esperada 1 entrada (forma de onda) e 1 saída com "
                f"{N_CLASSES} colunas de scores"
            )
        self._entrada = entradas[0].name
        self._saida = saidas[0].name

    def __call__(self, waveform: np.ndarray) -> np.ndarray:
        scores = self._sessao.run([self._saida], {self._entrada: waveform.astype(np.float32)})[0]
        return np.asarray(scores).reshape(-1, N_CLASSES)


class DetectorRespiratorio:
    def __init__(
        self, sessao: Sessao, nomes_classes: Sequence[str], cfg: RespiratorioCfg
    ) -> None:
        if len(nomes_classes) != N_CLASSES:
            raise ErroDeModelo(
                f"o mapa de classes tem {len(nomes_classes)} nomes; esperados {N_CLASSES}")
        indices = {nome: i for i, nome in enumerate(nomes_classes)}
        faltando = [c.classe_audioset for c in cfg.classes if c.classe_audioset not in indices]
        if faltando:
            raise ErroDeConfiguracao(
                f"respiratorio.classes: {faltando} não existem no mapa de classes do YAMNet"
            )
        self._sessao = sessao
        self._cfg = cfg
        self._indices = {c.classe_audioset: indices[c.classe_audioset] for c in cfg.classes}

    def detectar(
        self,
        sinal: np.ndarray,
        taxa_hz: int,
        *,
        ignorar_s: Sequence[tuple[float, float]] = (),
    ) -> list[EventoRespiratorio]:
        """Eventos no áudio todo. ``ignorar_s`` (falas do médico) não gera eventos do paciente."""
        if taxa_hz != TAXA_HZ:
            raise ValueError(f"o YAMNet exige {TAXA_HZ} Hz; recebido {taxa_hz} Hz")
        if sinal.size == 0:
            return []
        duracao_s = sinal.size / taxa_hz
        scores = self._sessao(sinal.astype(np.float32))
        centros = np.arange(scores.shape[0]) * PASSO_S + QUADRO_S / 2
        ignorado = np.zeros(scores.shape[0], dtype=bool)
        for inicio, fim in ignorar_s:
            ignorado |= (centros >= inicio) & (centros < fim)

        eventos: list[EventoRespiratorio] = []
        for classe in self._cfg.classes:
            coluna = scores[:, self._indices[classe.classe_audioset]]
            acima = np.flatnonzero((coluna >= self._cfg.score_minimo) & ~ignorado)
            for grupo in _agrupar(acima, self._cfg.lacuna_quadros):
                valores = coluna[grupo]
                eventos.append(EventoRespiratorio(
                    tipo=classe.tipo,
                    classe_audioset=classe.classe_audioset,
                    inicio_s=float(grupo[0] * PASSO_S),
                    fim_s=float(min(grupo[-1] * PASSO_S + QUADRO_S, duracao_s)),
                    score_max=float(valores.max()),
                    score_medio=float(valores.mean()),
                    quadros=len(grupo),
                    severidade=classe.severidade,
                ))
        if len(eventos) > self._cfg.max_eventos:
            log.warning("%d eventos respiratórios; mantidos os %d de maior score",
                        len(eventos), self._cfg.max_eventos)
            eventos = sorted(eventos, key=lambda e: e.score_max, reverse=True)[
                : self._cfg.max_eventos]
        return sorted(eventos, key=lambda e: (e.inicio_s, e.tipo))


def _agrupar(indices: np.ndarray, lacuna: int) -> list[np.ndarray]:
    """Divide quadros ordenados em grupos; até ``lacuna`` quadros faltando ainda é o mesmo grupo."""
    if indices.size == 0:
        return []
    cortes = np.flatnonzero(np.diff(indices) - 1 > lacuna) + 1
    return np.split(indices, cortes)


def carregar_detector(
    cfg: RespiratorioCfg, caminho_modelo: str | Path, caminho_classes: str | Path | None = None
) -> DetectorRespiratorio | None:
    """Detector com o YAMNet ONNX, ou ``None`` (com aviso) se o modelo não foi baixado.

    Sem o modelo a análise segue sem eventos respiratórios, e o relatório diz isso
    (``respiratorio.disponivel = false``), em vez de falhar ou de fingir que não houve evento.
    """
    caminho_modelo = Path(caminho_modelo)
    classes = Path(caminho_classes) if caminho_classes else caminho_modelo.with_name(
        "yamnet_class_map.csv")
    if not caminho_modelo.is_file() or not classes.is_file():
        log.warning(
            "YAMNet indisponível (%s): a análise segue sem eventos respiratórios. "
            "Baixe com: python scripts/baixar_modelos_audio.py", caminho_modelo)
        return None
    return DetectorRespiratorio(
        SessaoYamnetOnnx(caminho_modelo), carregar_nomes_classes(classes), cfg)
