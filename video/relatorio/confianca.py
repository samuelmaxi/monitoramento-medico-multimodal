"""Confiança de cada desvio no relatório da US09.

O ``score`` do evento mede a **intensidade** do desvio (0,5 ao passar o limiar, 1,0 na
saturação), não a certeza de que ele aconteceu. A confiança vem da fonte de cada regra:

- regras de pose (OpenPose): média da confiança dos keypoints que a medida usa, nos quadros do
  trecho do desvio. A ``cobertura`` é a % desses quadros em que todos os keypoints foram vistos;
- presença em área crítica (YOLOv8): média da confiança da detecção na entrada e na saída
  (já é o ``score`` do desvio, vindo dos eventos da US07).

Desvio sem keypoints ou detecções para estimar fica com confiança ``None`` e é marcado como de
baixa confiança: a equipe deve conferir o quadro de evidência.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from video.desvios.modelos import Desvio
from video.pose.angles import ASYMMETRIES, JOINT_ANGLES, TRUNK_COLUMN
from video.pose.sequence import Keypoint, PoseSequence

from .config import ConfigConfianca

ORIGEM_POSE = "OpenPose: média da confiança dos keypoints da medida nos quadros do trecho"
ORIGEM_DETECCAO = "YOLOv8: média da confiança da detecção na entrada e na saída da área"
ORIGEM_INDISPONIVEL = "sem keypoints ou detecções suficientes para estimar a confiança"

# Medidas de queda e de giro do tronco: pescoço e quadril (MidHip, ou os dois quadris).
_MEDIDAS_DO_TRONCO = {TRUNK_COLUMN, "descida_quadril"}
_FOLGA_S = 1e-6


@dataclass(frozen=True)
class Confianca:
    valor: float | None
    origem: str
    cobertura_pct: float | None
    baixa: bool


def _colunas(desvio: Desvio) -> list[str]:
    """Colunas de ângulo da regra: a medida e, nas repetições, as da série de referência."""
    colunas = [desvio.medida]
    colunas += [
        chave.removeprefix("referencia_")
        for chave in desvio.features
        if chave.startswith("referencia_")
    ]
    return list(dict.fromkeys(colunas))


def _nomes(coluna: str, keypoints: Mapping[str, Keypoint]) -> list[str]:
    if coluna in JOINT_ANGLES:
        return list(JOINT_ANGLES[coluna])
    if coluna in ASYMMETRIES:
        return [nome for lado in ASYMMETRIES[coluna] for nome in JOINT_ANGLES[lado]]
    if coluna in _MEDIDAS_DO_TRONCO:
        quadril = ["MidHip"] if keypoints.get("MidHip") is not None else ["LHip", "RHip"]
        return ["Neck", *quadril]
    return []


def _nomes_do_quadro(colunas: Iterable[str], keypoints: Mapping[str, Keypoint]) -> list[str]:
    nomes = [nome for coluna in colunas for nome in _nomes(coluna, keypoints)]
    return list(dict.fromkeys(nomes))


def da_pose(desvio: Desvio, pose: PoseSequence, cfg: ConfigConfianca) -> Confianca:
    colunas = _colunas(desvio)
    quadros = [
        f
        for f in pose.frames
        if desvio.inicio_s - _FOLGA_S <= f.timestamp_ms / 1000 <= desvio.fim_s + _FOLGA_S
    ]
    por_quadro: list[float] = []
    completos = avaliados = 0
    for quadro in quadros:
        nomes = _nomes_do_quadro(colunas, quadro.keypoints)
        if not nomes:
            continue
        avaliados += 1
        vistos = [quadro.keypoints[n][2] for n in nomes if quadro.keypoints.get(n) is not None]
        if vistos:
            por_quadro.append(sum(vistos) / len(vistos))
        if len(vistos) == len(nomes):
            completos += 1
    if not por_quadro:
        return Confianca(None, ORIGEM_INDISPONIVEL, None, True)
    valor = round(sum(por_quadro) / len(por_quadro), 4)
    cobertura = round(100 * completos / avaliados, 2)
    baixa = valor < cfg.minima or cobertura < cfg.cobertura_minima_pct
    return Confianca(valor, ORIGEM_POSE, cobertura, baixa)


def da_deteccao(desvio: Desvio, cfg: ConfigConfianca) -> Confianca:
    valor = round(float(desvio.score), 4)
    return Confianca(valor, ORIGEM_DETECCAO, None, valor < cfg.minima)


def avaliar(desvio: Desvio, pose: PoseSequence | None, cfg: ConfigConfianca) -> Confianca:
    """Confiança do desvio, pela fonte da regra que o gerou."""
    if desvio.regra.startswith("area."):
        return da_deteccao(desvio, cfg)
    if pose is None:
        return Confianca(None, ORIGEM_INDISPONIVEL, None, True)
    return da_pose(desvio, pose, cfg)


__all__ = ["Confianca", "avaliar", "da_deteccao", "da_pose"]
