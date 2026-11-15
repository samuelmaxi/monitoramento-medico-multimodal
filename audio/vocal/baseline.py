"""Baseline do próprio paciente: compara um áudio com os anteriores dele (US12).

Para cada métrica, mediana e MAD do histórico; o desvio é ``|x - mediana| / dispersão`` com
``dispersão = max(1,4826 * MAD, piso da métrica)``. O piso (``baseline.metricas`` no YAML) evita
um z enorme quando o histórico é quase constante. É o mesmo z robusto da detecção de sinais vitais
(``sinais``), mas o histórico aqui são poucos áudios, e não horas de medições.

O histórico fica em um JSON por paciente (``<pasta>/<pt_...>.json``), indexado só pelo pseudônimo.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from contratos.pseudonimizacao import PADRAO_PACIENTE

from .config import BaselineCfg

_ESCALA_MAD = 1.4826
_TRAVA = threading.Lock()


@dataclass(frozen=True)
class RegistroBaseline:
    audio_id: str
    metricas: Mapping[str, float | None]


@dataclass(frozen=True)
class DesvioMetrica:
    metrica: str
    valor: float
    mediana: float
    dispersao: float
    z: float

    @property
    def direcao(self) -> str:
        return "acima" if self.z > 0 else "abaixo"

    def para_dict(self) -> dict[str, float | str]:
        return {
            "metrica": self.metrica,
            "valor": round(self.valor, 4),
            "mediana": round(self.mediana, 4),
            "dispersao": round(self.dispersao, 4),
            "z": round(self.z, 2),
            "direcao": self.direcao,
        }


@dataclass(frozen=True)
class ComparacaoBaseline:
    audios: int
    """Quantos áudios anteriores do paciente havia na baseline."""
    suficiente: bool
    medianas: Mapping[str, float]
    desvios: tuple[DesvioMetrica, ...]

    def para_dict(self) -> dict[str, object]:
        return {
            "audios": self.audios,
            "suficiente": self.suficiente,
            "medianas": {k: round(v, 4) for k, v in self.medianas.items()},
            "desvios": [d.para_dict() for d in self.desvios],
        }


class RepositorioBaseline:
    def __init__(self, pasta: str | Path) -> None:
        self.pasta = Path(pasta)

    def _caminho(self, patient_id: str) -> Path:
        # O nome do arquivo vem do ID: só o pseudônimo é aceito (sem '/', '..' nem ID de origem).
        if not re.fullmatch(PADRAO_PACIENTE, patient_id):
            raise ValueError("a baseline é indexada pelo pseudônimo do paciente (pt_ + 24 hex)")
        return self.pasta / f"{patient_id}.json"

    def carregar(self, patient_id: str) -> list[RegistroBaseline]:
        caminho = self._caminho(patient_id)
        if not caminho.is_file():
            return []
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        return [RegistroBaseline(r["audio_id"], r["metricas"]) for r in dados["audios"]]

    def registrar(
        self, patient_id: str, audio_id: str, metricas: Mapping[str, float | None], *, maximo: int
    ) -> None:
        """Acrescenta o áudio (troca o registro do mesmo ``audio_id``) e mantém os últimos."""
        caminho = self._caminho(patient_id)
        with _TRAVA:
            historico = [r for r in self.carregar(patient_id) if r.audio_id != audio_id]
            historico.append(RegistroBaseline(audio_id, dict(metricas)))
            historico = historico[-maximo:]
            self.pasta.mkdir(parents=True, exist_ok=True)
            temporario = caminho.with_suffix(".json.tmp")
            temporario.write_text(
                json.dumps({"patient_id": patient_id,
                            "audios": [{"audio_id": r.audio_id, "metricas": dict(r.metricas)}
                                       for r in historico]}, ensure_ascii=False, indent=2),
                encoding="utf-8")
            os.replace(temporario, caminho)


def comparar(
    historico: Sequence[RegistroBaseline],
    atuais: Mapping[str, float | None],
    cfg: BaselineCfg,
    *,
    ignorar_audio_id: str | None = None,
) -> ComparacaoBaseline:
    """Compara as métricas deste áudio com a baseline. Sem áudios suficientes, não há desvios."""
    anteriores = [r for r in historico if r.audio_id != ignorar_audio_id]
    medianas: dict[str, float] = {}
    desvios: list[DesvioMetrica] = []
    for metrica, piso in cfg.metricas.items():
        valores = np.array(
            [r.metricas.get(metrica) for r in anteriores if r.metricas.get(metrica) is not None],
            dtype=float)
        if valores.size < cfg.audios_minimos:
            continue
        mediana = float(np.median(valores))
        medianas[metrica] = mediana
        atual = atuais.get(metrica)
        if atual is None:
            continue
        mad = float(np.median(np.abs(valores - mediana)))
        dispersao = max(_ESCALA_MAD * mad, piso)
        z = (atual - mediana) / dispersao
        if abs(z) >= cfg.z_minimo:
            desvios.append(DesvioMetrica(metrica, float(atual), mediana, dispersao, float(z)))
    desvios.sort(key=lambda d: abs(d.z), reverse=True)
    return ComparacaoBaseline(
        audios=len(anteriores),
        suficiente=len(anteriores) >= cfg.audios_minimos,
        medianas=medianas,
        desvios=tuple(desvios),
    )
