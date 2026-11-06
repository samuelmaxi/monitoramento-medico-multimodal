"""Segmentos de fala do paciente: da transcrição do Azure Speech (US11) ou, sem ela, por energia.

A transcrição traz as falas diarizadas com ``offset_ms``/``duracao_ms`` e as palavras de cada uma.
Como no ``audio.text`` (US13), o JSON com várias vozes e sem ``medico_falante`` é recusado: errar
quem é o médico mediria a voz dele no lugar da do paciente. A exceção é o áudio de **um só
falante** (a gravação do próprio paciente, como um áudio de voz do WhatsApp), tratado como
paciente e marcado como tal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from audio.sinal import janela_em_amostras, para_db, rms_por_quadro, trechos

from .config import ConfigVocal
from .erros import TranscricaoInvalida

FONTE_TRANSCRICAO = "transcricao"
FONTE_VAD = "vad"


@dataclass(frozen=True)
class SegmentoFala:
    indice: int
    inicio_ms: int
    fim_ms: int
    palavras: int | None
    """Contagem de palavras; ``None`` quando não há transcrição (VAD)."""
    contiguo_ao_anterior: bool
    """Nenhuma fala do médico entre este segmento e o anterior: o intervalo é pausa do paciente."""

    @property
    def duracao_s(self) -> float:
        return (self.fim_ms - self.inicio_ms) / 1000


@dataclass(frozen=True)
class SegmentosDoPaciente:
    segmentos: tuple[SegmentoFala, ...]
    intervalos_medico_ms: tuple[tuple[int, int], ...]
    fonte: str
    papel_assumido: bool
    """``True`` quando o áudio tinha um só falante e ele foi tratado como paciente."""
    notas: tuple[str, ...] = ()

    @property
    def intervalos_medico_s(self) -> tuple[tuple[float, float], ...]:
        return tuple((a / 1000, b / 1000) for a, b in self.intervalos_medico_ms)


def _inteiro_nao_negativo(valor: Any, nome: str, indice: int) -> int:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or valor < 0:
        raise TranscricaoInvalida(f"falas[{indice}].{nome} precisa ser um número >= 0")
    return int(valor)


def segmentos_da_transcricao(
    transcricao: Mapping[str, Any], medico_falante: int | None = None
) -> SegmentosDoPaciente:
    """Segmentos do paciente no JSON da US11. ``medico_falante`` tem precedência sobre o do JSON."""
    falas = transcricao.get("falas")
    if not isinstance(falas, Sequence) or isinstance(falas, (str, bytes)) or not falas:
        raise TranscricaoInvalida("a transcrição não tem falas")
    validadas: list[tuple[int, int, Mapping[str, Any]]] = []
    for i, fala in enumerate(falas):
        if not isinstance(fala, Mapping):
            raise TranscricaoInvalida(f"falas[{i}] precisa ser um objeto")
        inicio = _inteiro_nao_negativo(fala.get("offset_ms"), "offset_ms", i)
        fim = inicio + _inteiro_nao_negativo(fala.get("duracao_ms"), "duracao_ms", i)
        validadas.append((inicio, fim, fala))
    validadas.sort(key=lambda trecho: trecho[0])

    notas: list[str] = []
    sem_falante = sum(1 for _, _, f in validadas if f.get("falante") is None)
    if sem_falante:
        notas.append(f"{sem_falante} fala(s) sem falante na diarização foram ignoradas")
    ordenadas = [(a, b, f) for a, b, f in validadas if f.get("falante") is not None]
    if not ordenadas:
        raise TranscricaoInvalida("nenhuma fala tem falante identificado pela diarização")

    falantes = {int(f["falante"]) for _, _, f in ordenadas}
    medico = medico_falante if medico_falante is not None else transcricao.get("medico_falante")
    assumido = False
    if medico is None:
        if len(falantes) > 1:
            raise TranscricaoInvalida(
                f"a transcrição tem {len(falantes)} falantes e não informa quem é o médico; "
                "passe medico_falante (a voz do médico não deve ser medida como a do paciente)"
            )
        assumido = True
        notas.append("áudio de um único falante tratado como paciente")
    medico_int = None if medico is None else int(medico)

    segmentos: list[SegmentoFala] = []
    medico_ms: list[tuple[int, int]] = []
    anterior_foi_paciente = False
    for inicio, fim, fala in ordenadas:
        if medico_int is not None and int(fala["falante"]) == medico_int:
            medico_ms.append((inicio, fim))
            anterior_foi_paciente = False
            continue
        palavras = fala.get("palavras")
        contagem = len(palavras) if isinstance(palavras, Sequence) and palavras else len(
            str(fala.get("texto", "")).split())
        segmentos.append(SegmentoFala(
            indice=len(segmentos), inicio_ms=inicio, fim_ms=fim, palavras=contagem,
            contiguo_ao_anterior=anterior_foi_paciente,
        ))
        anterior_foi_paciente = True
    return SegmentosDoPaciente(
        segmentos=tuple(segmentos), intervalos_medico_ms=tuple(medico_ms),
        fonte=FONTE_TRANSCRICAO, papel_assumido=assumido, notas=tuple(notas),
    )


def segmentos_por_vad(sinal: np.ndarray, taxa_hz: int, cfg: ConfigVocal) -> SegmentosDoPaciente:
    """Segmentos de fala por energia, sem transcrição: o áudio inteiro é tratado como o paciente.

    Trechos de fala separados por silêncio menor que ``pausa_entre_segmentos_s`` formam um só
    segmento. Sem palavras, taxa de fala e de articulação não são calculadas.
    """
    janela = janela_em_amostras(taxa_hz, cfg.segmentos.vad.janela_ms)
    db = para_db(rms_por_quadro(sinal, janela))
    if db.size == 0:
        return SegmentosDoPaciente((), (), FONTE_VAD, True, ("áudio sem amostras",))
    nivel = float(np.percentile(db, 95))
    if nivel < cfg.pausas.nivel_minimo_fala_db:  # só silêncio ou ruído de ambiente
        return SegmentosDoPaciente((), (), FONTE_VAD, True, ("sem fala audível no áudio",))
    fala = db >= nivel + cfg.pausas.limiar_relativo_db
    juntar = max(1, round(cfg.segmentos.vad.pausa_entre_segmentos_s * taxa_hz / janela))

    blocos: list[list[int]] = []
    for inicio, fim in trechos(fala):
        if blocos and inicio - blocos[-1][1] < juntar:
            blocos[-1][1] = fim
        else:
            blocos.append([inicio, fim])
    segmentos = tuple(
        SegmentoFala(
            indice=i, inicio_ms=round(b[0] * janela / taxa_hz * 1000),
            fim_ms=round(min(b[1] * janela, sinal.size) / taxa_hz * 1000),
            palavras=None, contiguo_ao_anterior=i > 0,
        )
        for i, b in enumerate(blocos)
    )
    return SegmentosDoPaciente(
        segmentos=segmentos, intervalos_medico_ms=(), fonte=FONTE_VAD, papel_assumido=True,
        notas=("sem transcrição: segmentos por energia, sem taxa de fala",),
    )
