"""Avaliação da camada de ML com anomalias injetadas e marcadas sobre séries reais.

A série é dividida, por paciente e sinal, em slots de 6 h (depois de 12 h de
baseline). Metade dos slots recebe uma anomalia sintética de piora clínica
(degrau ou rampa) e vira rótulo positivo; os demais são negativos. Um slot é
predito positivo quando o detector emite ``desvio_baseline`` ou
``tendencia_deterioracao`` para aquele sinal dentro do slot. Depois de cada
slot positivo vem um slot de guarda, não pontuado, para o retorno ao normal
não contar como falso positivo.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd

from .detector import ConfigDetector, DetectorSinais, Medicao

SLOT = timedelta(hours=6)
AQUECIMENTO = timedelta(hours=12)
MIN_PONTOS_SLOT = 4
MIN_PONTOS_ANTES = 6
# Piora clínica injetada: FC e FR sobem; PA e SpO2 caem.
DELTA = {"fc": 30.0, "pas": -30.0, "pad": -18.0, "spo2": -6.0, "fr": 9.0}
TIPOS = ("degrau", "rampa")
EVENTOS_ML = {"desvio_baseline", "tendencia_deterioracao"}


@dataclass(frozen=True)
class Matriz:
    vp: int
    fp: int
    fn: int
    vn: int

    @property
    def recall(self) -> float:
        return self.vp / (self.vp + self.fn) if self.vp + self.fn else 0.0

    @property
    def precisao(self) -> float:
        return self.vp / (self.vp + self.fp) if self.vp + self.fp else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precisao, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def para_dict(self) -> dict:
        return {
            "matriz_confusao": {"vp": self.vp, "fp": self.fp, "fn": self.fn, "vn": self.vn},
            "recall": round(self.recall, 4),
            "precisao": round(self.precisao, 4),
            "f1": round(self.f1, 4),
            "n": self.vp + self.fp + self.fn + self.vn,
        }


def montar_rotulos(df: pd.DataFrame, semente: int = 42) -> pd.DataFrame:
    """Um slot por linha: ``subject_id, sinal, inicio, fim, tipo`` (``tipo`` vazio = negativo)."""
    rng = np.random.default_rng(semente)
    slots = []
    for (subject_id, sinal), serie in df[df.sinal.isin(DELTA)].groupby(["subject_id", "sinal"]):
        instantes = serie.instante
        inicio, fim_serie = instantes.iloc[0] + AQUECIMENTO, instantes.iloc[-1]
        guarda = False
        while inicio + SLOT <= fim_serie:
            fim = inicio + SLOT
            no_slot = ((instantes >= inicio) & (instantes < fim)).sum()
            antes = ((instantes >= inicio - AQUECIMENTO) & (instantes < inicio)).sum()
            if guarda:
                guarda = False
            elif no_slot >= MIN_PONTOS_SLOT and antes >= MIN_PONTOS_ANTES:
                tipo = str(rng.choice(TIPOS)) if rng.random() < 0.5 else ""
                slots.append((subject_id, sinal, inicio, fim, tipo))
                guarda = bool(tipo)
            inicio = fim
    return pd.DataFrame(slots, columns=["subject_id", "sinal", "inicio", "fim", "tipo"])


def injetar(df: pd.DataFrame, rotulos: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for r in rotulos[rotulos.tipo != ""].itertuples():
        m = (
            (df.subject_id == r.subject_id)
            & (df.sinal == r.sinal)
            & (df.instante >= r.inicio)
            & (df.instante < r.fim)
        )
        fracao = (df.loc[m, "instante"] - r.inicio) / SLOT
        efeito = DELTA[r.sinal] * (
            1.0 if r.tipo == "degrau" else 2 * fracao
        )  # rampa chega a 2δ no fim
        df.loc[m, "valor"] = df.loc[m, "valor"] + efeito
    df.loc[df.sinal == "spo2", "valor"] = df.loc[df.sinal == "spo2", "valor"].clip(upper=100)
    return df


def detectar(
    df: pd.DataFrame, pseudonimos: dict, config: ConfigDetector
) -> tuple[list, DetectorSinais, float]:
    """Roda o detector medição a medição, em ordem de tempo.

    Devolve eventos, detector e ms por medição.
    """
    detector = DetectorSinais(config, sintetico=True)
    eventos = []
    t0 = time.perf_counter()
    for linha in df.itertuples():
        eventos += detector.processar(
            Medicao(
                pseudonimos[linha.subject_id],
                linha.instante.to_pydatetime(),
                linha.sinal,
                linha.valor,
            )
        )
    ms = (time.perf_counter() - t0) * 1000 / max(len(df), 1)
    return eventos, detector, ms


def avaliar(rotulos: pd.DataFrame, eventos: list, pseudonimos: dict) -> pd.DataFrame:
    """Acrescenta a coluna ``predito`` aos rótulos."""
    achados = [
        (e.patient_id, e.evidence.features["sinal"], e.timestamp)
        for e in eventos
        if e.event_type in EVENTOS_ML
    ]
    achados = pd.DataFrame(achados, columns=["paciente", "sinal", "instante"])
    predito = []
    for r in rotulos.itertuples():
        a = achados[(achados.paciente == pseudonimos[r.subject_id]) & (achados.sinal == r.sinal)]
        predito.append(bool(((a.instante >= r.inicio) & (a.instante < r.fim)).any()))
    return rotulos.assign(predito=predito)


def matriz(resultado: pd.DataFrame) -> Matriz:
    real, pred = resultado.tipo != "", resultado.predito
    return Matriz(
        vp=int((real & pred).sum()),
        fp=int((~real & pred).sum()),
        fn=int((real & ~pred).sum()),
        vn=int((~real & ~pred).sum()),
    )
