"""Avaliação de detecção pelo critério PASCAL VOC (IoU >= 0,50).

Implementa o critério de acerto do projeto de forma **explícita e independente
de framework**: uma detecção conta como correta (verdadeiro positivo) quando casa
com uma verdade de terra da **mesma classe** e IoU ``>=`` limiar (padrão 0,50,
o mesmo da métrica ``mAP@0.5`` / PASCAL VOC). A partir desse casamento calcula
TP/FP/FN, precisão, revocação, F1 e a AP@0.5 por classe (interpolação em todos
os pontos, VOC2010+), devolvendo o ``mAP@0.5``.

O casamento é *greedy* por confiança, um-para-um e por classe, exatamente como no
procedimento VOC: cada predição disputa a melhor verdade ainda livre na sua
imagem; verdades não casadas viram falsos negativos. Nada é inventado — sem
verdades de terra o ``mAP@0.5`` fica indefinido (``None``), nunca estimado.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .geometria import iou
from .modelos import Caixa

LIMIAR_IOU_PADRAO = 0.5
"""IoU mínimo para uma detecção contar como acerto (PASCAL VOC / mAP@0.5)."""


@dataclass(frozen=True, slots=True)
class Verdade:
    """Uma caixa anotada (ground truth) em pixels, ligada a uma imagem."""

    imagem: str
    class_id: int
    caixa: Caixa

    def __post_init__(self) -> None:
        if self.class_id < 0:
            raise ValueError(f"class_id negativo: {self.class_id}")
        if not self.imagem:
            raise ValueError("verdade sem nome de imagem")


@dataclass(frozen=True, slots=True)
class Predicao:
    """Uma detecção com classe, confiança e caixa em pixels."""

    imagem: str
    class_id: int
    confianca: float
    caixa: Caixa

    def __post_init__(self) -> None:
        if self.class_id < 0:
            raise ValueError(f"class_id negativo: {self.class_id}")
        if not 0.0 <= self.confianca <= 1.0:
            raise ValueError(f"confianca fora de [0, 1]: {self.confianca}")


@dataclass(frozen=True, slots=True)
class CasamentoDeteccao:
    """Veredito de uma predição contra as verdades: acerto e IoU do casamento."""

    imagem: str
    class_id: int
    confianca: float
    iou: float
    verdadeiro_positivo: bool
    indice_verdade: int | None


@dataclass(frozen=True, slots=True)
class MetricasClasse:
    """Métricas de uma classe: contagens, precisão/revocação/F1 e AP@0.5."""

    class_id: int
    verdadeiros_positivos: int
    falsos_positivos: int
    falsos_negativos: int
    precisao: float
    recall: float
    f1: float
    ap_50: float
    total_verdades: int

    def para_dict(self) -> dict[str, float | int]:
        return {
            "class_id": self.class_id,
            "verdadeiros_positivos": self.verdadeiros_positivos,
            "falsos_positivos": self.falsos_positivos,
            "falsos_negativos": self.falsos_negativos,
            "precisao": self.precisao,
            "recall": self.recall,
            "f1": self.f1,
            "ap_50": self.ap_50,
            "total_verdades": self.total_verdades,
        }


@dataclass(frozen=True, slots=True)
class ResultadoAvaliacaoDeteccao:
    """Relatório de avaliação no critério IoU >= limiar (VOC / mAP@0.5)."""

    limiar_iou: float
    imagens: int
    predicoes: int
    total_verdades: int
    verdadeiros_positivos: int
    falsos_positivos: int
    falsos_negativos: int
    precisao: float
    recall: float
    f1: float
    mapa_50: float | None
    por_classe: tuple[MetricasClasse, ...] = field(default_factory=tuple)

    @property
    def medido(self) -> bool:
        """``True`` quando há verdades de terra — senão o mAP é indefinido."""
        return self.mapa_50 is not None

    def para_dict(self) -> dict[str, Any]:
        return {
            "criterio": f"IoU >= {self.limiar_iou:g} (PASCAL VOC / mAP@0.5)",
            "limiar_iou": self.limiar_iou,
            "imagens": self.imagens,
            "predicoes": self.predicoes,
            "total_verdades": self.total_verdades,
            "verdadeiros_positivos": self.verdadeiros_positivos,
            "falsos_positivos": self.falsos_positivos,
            "falsos_negativos": self.falsos_negativos,
            "precisao": self.precisao,
            "recall": self.recall,
            "f1": self.f1,
            "mAP@0.5": self.mapa_50,
            "medido": self.medido,
            "por_classe": [m.para_dict() for m in self.por_classe],
        }

    def salvar_json(self, caminho: str | Path) -> Path:
        destino = Path(caminho)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(self.para_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return destino


def _curva_precisao_recall(
    casamentos: list[CasamentoDeteccao], total_verdades: int
) -> tuple[list[float], list[float]]:
    ordenados = sorted(casamentos, key=lambda c: c.confianca, reverse=True)
    precisoes: list[float] = []
    recalls: list[float] = []
    tp = 0
    fp = 0
    for casamento in ordenados:
        if casamento.verdadeiro_positivo:
            tp += 1
        else:
            fp += 1
        precisoes.append(tp / (tp + fp))
        recalls.append(tp / total_verdades)
    return precisoes, recalls


def ap_50(casamentos: list[CasamentoDeteccao], total_verdades: int) -> float:
    """AP@0.5 por interpolação em todos os pontos (VOC2010+).

    Ordena as predições por confiança decrescente, torna a precisão monótona
    não decrescente (da direita para a esquerda) e integra a área sob a curva
    precisão-revocação. Sem verdades de terra a AP é indefinida e vale ``0.0``.
    """
    if total_verdades <= 0:
        return 0.0
    precisoes, recalls = _curva_precisao_recall(casamentos, total_verdades)
    for i in range(len(precisoes) - 2, -1, -1):
        precisoes[i] = max(precisoes[i], precisoes[i + 1])
    ap = 0.0
    recall_anterior = 0.0
    for precisao, recall in zip(precisoes, recalls, strict=True):
        ap += precisao * (recall - recall_anterior)
        recall_anterior = recall
    return ap


def _ponto_otimo_f1(
    casamentos: list[CasamentoDeteccao], total_verdades: int
) -> tuple[float, float, float]:
    """Precisão, revocação e F1 no ponto de maior F1 da curva."""
    if total_verdades <= 0:
        return 0.0, 0.0, 0.0
    precisoes, recalls = _curva_precisao_recall(casamentos, total_verdades)
    melhor = (0.0, 0.0, 0.0)
    for precisao, recall in zip(precisoes, recalls, strict=True):
        f1 = 2 * precisao * recall / (precisao + recall) if precisao + recall else 0.0
        if f1 > melhor[2]:
            melhor = (precisao, recall, f1)
    return melhor


def casar_deteccoes(
    predicoes: list[Predicao],
    verdades: list[Verdade],
    *,
    limiar_iou: float = LIMIAR_IOU_PADRAO,
) -> list[CasamentoDeteccao]:
    """Casa predições com verdades por IoU >= ``limiar_iou`` (mesma classe/imagem).

    Greedy por confiança decrescente, um-para-um. Cada verdade é usada no máximo
    uma vez. Devolve um veredito por predição (TP/FP) — as verdades não casadas
    são contabilizadas como falsos negativos pelo chamador.
    """
    if not 0.0 <= limiar_iou <= 1.0:
        raise ValueError(f"limiar_iou fora de [0, 1]: {limiar_iou}")

    verdades_por_imagem: dict[str, list[tuple[int, Verdade]]] = defaultdict(list)
    for indice, verdade in enumerate(verdades):
        verdades_por_imagem[verdade.imagem].append((indice, verdade))

    predicoes_por_imagem: dict[str, list[Predicao]] = defaultdict(list)
    for predicao in predicoes:
        predicoes_por_imagem[predicao.imagem].append(predicao)

    casamentos: list[CasamentoDeteccao] = []
    for imagem, preds in predicoes_por_imagem.items():
        candidatas = verdades_por_imagem.get(imagem, [])
        usadas: set[int] = set()
        for predicao in sorted(preds, key=lambda p: p.confianca, reverse=True):
            melhor_iou = 0.0
            melhor_indice: int | None = None
            for indice, verdade in candidatas:
                if indice in usadas or verdade.class_id != predicao.class_id:
                    continue
                valor = iou(predicao.caixa, verdade.caixa)
                if valor > melhor_iou:
                    melhor_iou = valor
                    melhor_indice = indice
            if melhor_indice is not None and melhor_iou >= limiar_iou:
                usadas.add(melhor_indice)
                casamentos.append(
                    CasamentoDeteccao(
                        imagem=imagem,
                        class_id=predicao.class_id,
                        confianca=predicao.confianca,
                        iou=melhor_iou,
                        verdadeiro_positivo=True,
                        indice_verdade=melhor_indice,
                    )
                )
            else:
                casamentos.append(
                    CasamentoDeteccao(
                        imagem=imagem,
                        class_id=predicao.class_id,
                        confianca=predicao.confianca,
                        iou=melhor_iou,
                        verdadeiro_positivo=False,
                        indice_verdade=None,
                    )
                )
    return casamentos


def avaliar_deteccao(
    predicoes: list[Predicao],
    verdades: list[Verdade],
    *,
    limiar_iou: float = LIMIAR_IOU_PADRAO,
) -> ResultadoAvaliacaoDeteccao:
    """Avalia predições contra verdades no critério IoU >= ``limiar_iou``.

    Devolve contagens TP/FP/FN, precisão/revocação/F1 (no ponto de maior F1) e o
    ``mAP@0.5`` (média da AP@0.5 entre as classes com verdades). Sem verdades de
    terra o ``mAP@0.5`` é ``None`` — não medido, nunca fabricado.
    """
    casamentos = casar_deteccoes(predicoes, verdades, limiar_iou=limiar_iou)

    verdades_por_classe: dict[int, int] = defaultdict(int)
    for verdade in verdades:
        verdades_por_classe[verdade.class_id] += 1

    casamentos_por_classe: dict[int, list[CasamentoDeteccao]] = defaultdict(list)
    for casamento in casamentos:
        casamentos_por_classe[casamento.class_id].append(casamento)

    classes = sorted(set(verdades_por_classe) | set(casamentos_por_classe))
    metricas: list[MetricasClasse] = []
    for class_id in classes:
        do_classe = casamentos_por_classe.get(class_id, [])
        total = verdades_por_classe.get(class_id, 0)
        tp = sum(1 for c in do_classe if c.verdadeiro_positivo)
        fp = len(do_classe) - tp
        fn = total - tp
        precisao, recall, f1 = _ponto_otimo_f1(do_classe, total)
        metricas.append(
            MetricasClasse(
                class_id=class_id,
                verdadeiros_positivos=tp,
                falsos_positivos=fp,
                falsos_negativos=fn,
                precisao=precisao,
                recall=recall,
                f1=f1,
                ap_50=ap_50(do_classe, total),
                total_verdades=total,
            )
        )

    tp_total = sum(1 for c in casamentos if c.verdadeiro_positivo)
    fp_total = len(casamentos) - tp_total
    fn_total = len(verdades) - tp_total
    precisao, recall, f1 = _ponto_otimo_f1(casamentos, len(verdades))

    classes_com_verdade = [m for m in metricas if m.total_verdades > 0]
    mapa_50 = (
        sum(m.ap_50 for m in classes_com_verdade) / len(classes_com_verdade)
        if classes_com_verdade
        else None
    )

    imagens = len({p.imagem for p in predicoes} | {v.imagem for v in verdades})
    return ResultadoAvaliacaoDeteccao(
        limiar_iou=limiar_iou,
        imagens=imagens,
        predicoes=len(predicoes),
        total_verdades=len(verdades),
        verdadeiros_positivos=tp_total,
        falsos_positivos=fp_total,
        falsos_negativos=fn_total,
        precisao=precisao,
        recall=recall,
        f1=f1,
        mapa_50=mapa_50,
        por_classe=tuple(metricas),
    )


def anotacao_para_caixa(anotacao: Any, largura: int, altura: int) -> Caixa:
    """Converte uma :class:`~video.anotacoes.Anotacao` normalizada em ``Caixa`` px."""
    if largura <= 0 or altura <= 0:
        raise ValueError(f"dimensões de imagem inválidas: {largura}x{altura}")
    return Caixa(
        x1=anotacao.x1 * largura,
        y1=anotacao.y1 * altura,
        x2=anotacao.x2 * largura,
        y2=anotacao.y2 * altura,
    )


def ler_predicoes_jsonl(caminho: str | Path) -> list[Predicao]:
    """Lê predições de um JSONL: uma por linha.

    Campos aceitos por linha: ``imagem`` (nome do arquivo ou stem), ``class_id``,
    ``confianca`` e a caixa em ``caixa: [x1, y1, x2, y2]`` ou ``x1``/``y1``/``x2``/``y2``.
    """
    caminho = Path(caminho)
    if not caminho.is_file():
        raise FileNotFoundError(f"arquivo de predições não encontrado: {caminho}")
    predicoes: list[Predicao] = []
    for numero, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), start=1):
        linha = linha.strip()
        if not linha:
            continue
        try:
            registro = json.loads(linha)
        except json.JSONDecodeError as erro:
            raise ValueError(f"linha {numero}: JSON inválido em {caminho}") from erro
        predicoes.append(_predicao_de_registro(registro, numero))
    return predicoes


def _caixa_de_registro(registro: dict[str, Any], numero: int) -> Caixa:
    if "caixa" in registro:
        partes = registro["caixa"]
        if not isinstance(partes, (list, tuple)) or len(partes) != 4:
            raise ValueError(f"linha {numero}: 'caixa' deve ter 4 valores [x1, y1, x2, y2]")
        x1, y1, x2, y2 = (float(p) for p in partes)
    else:
        try:
            x1 = float(registro["x1"])
            y1 = float(registro["y1"])
            x2 = float(registro["x2"])
            y2 = float(registro["y2"])
        except KeyError as erro:
            raise ValueError(
                f"linha {numero}: informe 'caixa' [x1, y1, x2, y2] ou 'x1'/'y1'/'x2'/'y2'"
            ) from erro
    try:
        return Caixa(x1=x1, y1=y1, x2=x2, y2=y2)
    except ValueError as erro:
        raise ValueError(f"linha {numero}: caixa inválida ({erro})") from erro


def _predicao_de_registro(registro: dict[str, Any], numero: int) -> Predicao:
    if "imagem" not in registro or "class_id" not in registro:
        raise ValueError(f"linha {numero}: campos obrigatórios 'imagem' e 'class_id'")
    return Predicao(
        imagem=Path(str(registro["imagem"])).stem,
        class_id=int(registro["class_id"]),
        confianca=float(registro.get("confianca", 1.0)),
        caixa=_caixa_de_registro(registro, numero),
    )


def predicoes_de_detector(
    detector: Any,
    imagens_dir: str | Path,
    *,
    classes: tuple[int, ...] | None = None,
) -> list[Predicao]:
    """Roda um detector em todas as imagens de uma pasta e coleta as predições.

    ``detector`` é qualquer objeto com ``detectar(imagem, indice_quadro, tempo_s)``
    que devolva objetos com ``class_id``, ``confianca`` e ``caixa`` (ex.:
    :class:`~video.detector.DetectorYolo`). Mantém o desacoplamento: nenhuma
    dependência da Ultralytics é importada aqui.
    """
    import cv2

    imagens = Path(imagens_dir)
    if not imagens.is_dir():
        raise FileNotFoundError(f"pasta de imagens não encontrada: {imagens}")

    predicoes: list[Predicao] = []
    for arquivo in sorted(imagens.iterdir()):
        if arquivo.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
            continue
        imagem = cv2.imread(str(arquivo))
        if imagem is None:
            raise ValueError(f"não foi possível ler a imagem: {arquivo}")
        for deteccao in detector.detectar(imagem, indice_quadro=0, tempo_s=0.0):
            if classes is not None and deteccao.class_id not in classes:
                continue
            predicoes.append(
                Predicao(
                    imagem=arquivo.stem,
                    class_id=deteccao.class_id,
                    confianca=deteccao.confianca,
                    caixa=deteccao.caixa,
                )
            )
    return predicoes


def carregar_verdades(
    imagens_dir: str | Path,
    labels_dir: str | Path,
    *,
    classes: tuple[int, ...] | None = None,
) -> list[Verdade]:
    """Carrega verdades de terra em formato YOLO (``imagens/`` + ``labels/``).

    Lê cada imagem para obter ``largura``/``altura`` e converte as anotações
    normalizadas para ``Caixa`` em pixels. Arquivos de label vazios são imagens
    negativas válidas. ``classes``, quando informado, filtra as classes.
    """
    import cv2

    from .anotacoes import ler_label

    imagens = Path(imagens_dir)
    labels = Path(labels_dir)
    if not imagens.is_dir():
        raise FileNotFoundError(f"pasta de imagens não encontrada: {imagens}")
    if not labels.is_dir():
        raise FileNotFoundError(f"pasta de labels não encontrada: {labels}")

    verdades: list[Verdade] = []
    for arquivo in sorted(imagens.iterdir()):
        if arquivo.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
            continue
        imagem = cv2.imread(str(arquivo))
        if imagem is None:
            raise ValueError(f"não foi possível ler a imagem: {arquivo}")
        altura, largura = imagem.shape[:2]
        label = labels / f"{arquivo.stem}.txt"
        if not label.is_file():
            continue
        for anotacao in ler_label(label):
            if classes is not None and anotacao.class_id not in classes:
                continue
            verdades.append(
                Verdade(
                    imagem=arquivo.stem,
                    class_id=anotacao.class_id,
                    caixa=anotacao_para_caixa(anotacao, largura, altura),
                )
            )
    return verdades


__all__ = [
    "LIMIAR_IOU_PADRAO",
    "CasamentoDeteccao",
    "MetricasClasse",
    "Predicao",
    "ResultadoAvaliacaoDeteccao",
    "Verdade",
    "anotacao_para_caixa",
    "ap_50",
    "avaliar_deteccao",
    "carregar_verdades",
    "casar_deteccoes",
    "ler_predicoes_jsonl",
    "predicoes_de_detector",
]
