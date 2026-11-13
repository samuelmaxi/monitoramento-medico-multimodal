"""Análise vocal de um áudio já ingerido: de ``convertido.wav`` aos eventos da modalidade ``audio``.

Segue o caminho dos demais detectores: não conhece o barramento, só recebe um ``Emissor``
(``EmissorMemoria`` nos testes, ``EmissorJsonl`` isolado ou ``BarramentoLocal`` no pipeline).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import soundfile as sf

from audio.ingestao.config import ConfigIngestao
from audio.ingestao.preparo import preparar_copia_features
from audio.text.events import patient_pseudonym, relative_uri
from contratos import Emissor, EventoAchado, pseudonimizar_internacao
from contratos.pseudonimizacao import PADRAO_INTERNACAO

from .baseline import ComparacaoBaseline, RepositorioBaseline, comparar
from .biomarcadores import BiomarcadoresAudio, MedidaSegmento, agregar, medir_segmentos
from .classificacao import Classificacao, classificar
from .config import ConfigVocal
from .erros import ErroDeAnalise
from .eventos import ContextoEvento, montar_eventos, relatorio
from .respiratorio import DetectorRespiratorio, ResumoRespiratorio
from .segmentos import (
    SegmentosDoPaciente,
    segmentos_da_transcricao,
    segmentos_por_vad,
)

log = logging.getLogger("audio.vocal")


@dataclass(frozen=True)
class EntradaAnalise:
    audio_id: str
    patient_id: str
    """ID de origem ou pseudônimo. O de origem é pseudonimizado com ``PSEUDONYM_KEY``."""
    recorded_at: datetime
    """Início da gravação, em UTC: ``recorded_at`` + posição no áudio = ``timestamp`` do achado."""
    medico_falante: int | None = None
    transcricao: Mapping[str, Any] | None = None
    bed_id: str | None = None
    encounter_id: str | None = None
    sintetico: bool = False
    formato_origem: str | None = None
    """Extensão do áudio original (ex.: ``ogg``); decide a ressalva de áudio comprimido."""


AVISO_COMPRIMIDO = (
    "áudio de origem comprimido com perdas ({formato}): jitter, shimmer e HNR podem estar "
    "superestimados, e os limiares MDVP são de gravação de qualidade clínica"
)


@dataclass(frozen=True, eq=False)
class ResultadoVocal:
    audio_id: str
    patient_id: str
    biomarcadores: BiomarcadoresAudio
    medidas: list[MedidaSegmento]
    segmentos: SegmentosDoPaciente
    respiratorio: ResumoRespiratorio
    baseline: ComparacaoBaseline | None
    classificacao: Classificacao
    eventos: list[EventoAchado]
    relatorio: dict[str, Any]

    @property
    def classe(self) -> str:
        return self.classificacao.classe_principal


def _exigir_utc(instante: datetime) -> None:
    if instante.tzinfo is None or instante.utcoffset() != timedelta(0):
        raise ErroDeAnalise("recorded_at precisa estar em UTC (ex.: 2026-10-01T13:00:00Z)")


def _encounter(encounter_id: str | None) -> str | None:
    if not encounter_id:
        return None
    if re.fullmatch(PADRAO_INTERNACAO, encounter_id):
        return encounter_id
    return pseudonimizar_internacao(encounter_id)


def analisar(
    convertido: str | Path,
    entrada: EntradaAnalise,
    cfg_ingestao: ConfigIngestao,
    cfg: ConfigVocal,
    *,
    detector: DetectorRespiratorio | None = None,
    repositorio: RepositorioBaseline | None = None,
    atualizar_baseline: bool = True,
    emissor: Emissor | None = None,
    relatorio_uri: str | None = None,
) -> ResultadoVocal:
    """Mede, classifica e monta os eventos de ``convertido`` (WAV PCM 16 kHz mono da US10).

    Levanta :class:`ErroDeAnalise` se a entrada não puder ser analisada e
    ``ChavePseudonimizacaoAusente`` se faltar a ``PSEUDONYM_KEY``.
    """
    convertido = Path(convertido)
    _exigir_utc(entrada.recorded_at)
    paciente = patient_pseudonym(entrada.patient_id)  # falha cedo, antes de qualquer cálculo
    encounter = _encounter(entrada.encounter_id)

    sinal, taxa = sf.read(str(convertido), dtype="float32", always_2d=False)
    if sinal.ndim != 1 or taxa != cfg_ingestao.taxa_amostragem_hz:
        raise ErroDeAnalise(
            f"'{convertido.name}' precisa ser WAV mono de {cfg_ingestao.taxa_amostragem_hz} Hz "
            "(gerado por audio.ingestao)"
        )
    copia = preparar_copia_features(sinal, taxa, cfg_ingestao.copia_features)

    if entrada.transcricao is not None:
        segmentos = segmentos_da_transcricao(entrada.transcricao, entrada.medico_falante)
    else:
        segmentos = segmentos_por_vad(sinal, taxa, cfg)
    medidas = medir_segmentos(sinal, copia, segmentos.segmentos, cfg)
    biomarcadores = agregar(medidas)

    if detector is None:
        respiratorio = ResumoRespiratorio(disponivel=False)
    else:
        respiratorio = ResumoRespiratorio(
            disponivel=True,
            eventos=tuple(detector.detectar(sinal, taxa, ignorar_s=segmentos.intervalos_medico_s)),
        )

    comparacao = None
    if repositorio is not None:
        comparacao = comparar(
            repositorio.carregar(paciente), biomarcadores.como_metricas(), cfg.baseline,
            ignorar_audio_id=entrada.audio_id)
    classificacao = classificar(biomarcadores, respiratorio, comparacao, cfg)

    formato = (entrada.formato_origem or "").lower().lstrip(".") or None
    comprimido = formato in cfg.qualidade.formatos_com_perdas
    notas = segmentos.notas + ((AVISO_COMPRIMIDO.format(formato=formato),) if comprimido else ())
    contexto = ContextoEvento(
        audio_id=entrada.audio_id, patient_id=paciente, recorded_at=entrada.recorded_at,
        convertido_uri=relative_uri(convertido), relatorio_uri=relatorio_uri,
        bed_id=entrada.bed_id, encounter_id=encounter, sintetico=entrada.sintetico,
        fonte_segmentos=segmentos.fonte, papel_assumido=segmentos.papel_assumido,
        formato_origem=formato, audio_comprimido=comprimido,
    )
    eventos = montar_eventos(biomarcadores, respiratorio, comparacao, classificacao, cfg, contexto)
    resumo = relatorio(
        eventos, biomarcadores, [m.para_dict() for m in medidas], respiratorio, comparacao,
        classificacao, cfg, contexto, notas)

    if repositorio is not None and atualizar_baseline:
        if cfg.baseline.excluir_audios_alterados and classificacao.classes:
            log.info("[%s] áudio com achado de classe: fora da baseline do paciente",
                     entrada.audio_id)
        else:
            repositorio.registrar(paciente, entrada.audio_id, biomarcadores.como_metricas(),
                                  maximo=cfg.baseline.audios_maximos)
    if emissor is not None:
        emissor.emitir_varios(eventos)
    return ResultadoVocal(
        audio_id=entrada.audio_id, patient_id=paciente, biomarcadores=biomarcadores,
        medidas=medidas, segmentos=segmentos, respiratorio=respiratorio, baseline=comparacao,
        classificacao=classificacao, eventos=eventos, relatorio=resumo,
    )
