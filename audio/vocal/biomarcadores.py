"""Biomarcadores vocais por segmento de fala do paciente (US12), com Praat via parselmouth.

Por segmento: F0 (média e variação), jitter local, shimmer local, HNR, intensidade, taxa de fala
e proporção/duração das pausas. Depois, a agregação por áudio.

Duas fontes de sinal, de propósito:

- **cópia de features** (``audio.ingestao``: volume normalizado e silêncios longos removidos):
  F0, jitter, shimmer e HNR, que não dependem do volume nem das pausas;
- **``convertido.wav``** (sem alterações): pausas, taxa de fala e intensidade, justamente o que
  a normalização e o corte de silêncio apagariam ou falsificariam.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import parselmouth
from parselmouth.praat import call

from audio.ingestao.preparo import CopiaFeatures
from audio.sinal import janela_em_amostras, para_db, rms_por_quadro, trechos

from .config import ConfigVocal
from .erros import SemFalaDoPaciente
from .segmentos import SegmentoFala

PRAAT_VERSAO = parselmouth.PRAAT_VERSION
PARSELMOUTH_VERSAO = parselmouth.VERSION


@dataclass(frozen=True, eq=False)
class MedidaSegmento:
    """Medidas de um segmento. Os tempos são do áudio original (``convertido.wav``)."""

    indice: int
    inicio_s: float
    fim_s: float
    palavras: int | None
    pausas_s: float
    n_pausas: int
    lacuna_antes_s: float
    pausa_antes_s: float
    vozeado_s: float
    f0_hz: np.ndarray = field(repr=False)
    jitter_local_pct: float | None
    shimmer_local_pct: float | None
    hnr_db: float | None
    intensidade_db: float | None

    @property
    def duracao_s(self) -> float:
        return self.fim_s - self.inicio_s

    def para_dict(self) -> dict[str, float | int | None]:
        f0 = self.f0_hz
        return {
            "indice": self.indice,
            "inicio_s": round(self.inicio_s, 3),
            "fim_s": round(self.fim_s, 3),
            "duracao_s": round(self.duracao_s, 3),
            "palavras": self.palavras,
            "pausas_s": round(self.pausas_s, 3),
            "n_pausas": self.n_pausas,
            "vozeado_s": round(self.vozeado_s, 3),
            "f0_media_hz": round(float(f0.mean()), 2) if f0.size else None,
            "f0_desvio_st": _redondo(_desvio_st(f0)),
            "jitter_local_pct": _redondo(self.jitter_local_pct),
            "shimmer_local_pct": _redondo(self.shimmer_local_pct),
            "hnr_db": _redondo(self.hnr_db),
            "intensidade_db": _redondo(self.intensidade_db),
        }


@dataclass(frozen=True)
class BiomarcadoresAudio:
    """Biomarcadores do paciente no áudio inteiro. ``None`` = não mensurável neste áudio."""

    segmentos: int
    inicio_s: float
    fim_s: float
    palavras: int | None
    duracao_fala_s: float
    vozeado_s: float
    pausa_total_s: float
    n_pausas: int
    pausa_media_s: float
    proporcao_pausas: float
    taxa_fala_pps: float | None
    taxa_articulacao_pps: float | None
    f0_media_hz: float | None
    f0_desvio_st: float | None
    jitter_local_pct: float | None
    shimmer_local_pct: float | None
    hnr_db: float | None
    intensidade_db: float | None

    def como_metricas(self) -> dict[str, float | None]:
        """As métricas que entram na baseline do paciente (nomes de ``baseline.metricas``)."""
        return {
            "jitter_local_pct": self.jitter_local_pct,
            "shimmer_local_pct": self.shimmer_local_pct,
            "hnr_db": self.hnr_db,
            "f0_media_hz": self.f0_media_hz,
            "f0_desvio_st": self.f0_desvio_st,
            "intensidade_db": self.intensidade_db,
            "proporcao_pausas": self.proporcao_pausas,
            "taxa_fala_pps": self.taxa_fala_pps,
            "taxa_articulacao_pps": self.taxa_articulacao_pps,
        }

    def para_dict(self) -> dict[str, float | int | None]:
        return {
            "segmentos": self.segmentos,
            "inicio_s": round(self.inicio_s, 3),
            "fim_s": round(self.fim_s, 3),
            "palavras": self.palavras,
            "duracao_fala_s": round(self.duracao_fala_s, 3),
            "vozeado_s": round(self.vozeado_s, 3),
            "pausa_total_s": round(self.pausa_total_s, 3),
            "n_pausas": self.n_pausas,
            "pausa_media_s": round(self.pausa_media_s, 3),
            **{k: _redondo(v) for k, v in self.como_metricas().items()},
        }


def _redondo(valor: float | None, casas: int = 3) -> float | None:
    return None if valor is None else round(float(valor), casas)


def _ou_none(valor: object) -> float | None:
    """Praat devolve NaN para o que não consegue medir; o contrato não aceita NaN."""
    try:
        numero = float(valor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return numero if math.isfinite(numero) else None


def _desvio_st(f0_hz: np.ndarray) -> float | None:
    """Desvio padrão de F0 em semitonos em torno da média (independe do registro da voz)."""
    if f0_hz.size < 2:
        return None
    return float(np.std(12.0 * np.log2(f0_hz / f0_hz.mean())))


def _praat_voz(
    sinal: np.ndarray, taxa_hz: int, cfg: ConfigVocal
) -> tuple[np.ndarray, float, float | None, float | None, float | None]:
    """(F0 dos quadros vozeados, segundos vozeados, jitter %, shimmer %, HNR dB)."""
    p = cfg.praat
    som = parselmouth.Sound(sinal.astype(np.float64), sampling_frequency=float(taxa_hz))
    pitch = som.to_pitch(time_step=p.passo_s, pitch_floor=p.pitch_floor_hz,
                         pitch_ceiling=p.pitch_ceiling_hz)
    f0 = pitch.selected_array["frequency"]
    f0 = f0[f0 > 0]
    vozeado_s = f0.size * p.passo_s
    if vozeado_s < p.vozeado_minimo_s:
        return f0, vozeado_s, None, None, None
    try:
        pulsos = call(som, "To PointProcess (periodic, cc)", p.pitch_floor_hz, p.pitch_ceiling_hz)
        jitter = call(pulsos, "Get jitter (local)", 0, 0, p.periodo_minimo_s, p.periodo_maximo_s,
                      p.fator_maximo_periodo)
        shimmer = call([som, pulsos], "Get shimmer (local)", 0, 0, p.periodo_minimo_s,
                       p.periodo_maximo_s, p.fator_maximo_periodo, p.fator_maximo_amplitude)
    except parselmouth.PraatError:  # poucos períodos consecutivos para medir
        jitter = shimmer = math.nan
    try:
        harmonicidade = som.to_harmonicity_cc(
            time_step=p.passo_s, minimum_pitch=p.pitch_floor_hz,
            silence_threshold=p.hnr_limiar_silencio, periods_per_window=p.hnr_periodos_por_janela)
        hnr = call(harmonicidade, "Get mean", 0, 0)
    except parselmouth.PraatError:
        hnr = math.nan
    jitter_pct, shimmer_pct = _ou_none(jitter), _ou_none(shimmer)
    return (
        f0, vozeado_s,
        None if jitter_pct is None else jitter_pct * 100.0,
        None if shimmer_pct is None else shimmer_pct * 100.0,
        _ou_none(hnr),
    )


def _intensidade_db(sinal: np.ndarray, taxa_hz: int, cfg: ConfigVocal) -> float | None:
    """Média da intensidade (dB do Praat) nos quadros de fala, sem as pausas do segmento."""
    p = cfg.praat
    if sinal.size / taxa_hz < 6.4 / p.pitch_floor_hz:  # janela mínima do Praat
        return None
    som = parselmouth.Sound(sinal.astype(np.float64), sampling_frequency=float(taxa_hz))
    valores = som.to_intensity(minimum_pitch=p.pitch_floor_hz, time_step=p.passo_s).values[0]
    valores = valores[np.isfinite(valores)]
    if valores.size == 0:
        return None
    fala = valores >= valores.max() + cfg.pausas.limiar_relativo_db
    return _ou_none(valores[fala].mean())


def nivel_de_fala_db(
    convertido: np.ndarray, taxa_hz: int, segmentos: Sequence[SegmentoFala], cfg: ConfigVocal
) -> float | None:
    """Nível de fala do paciente (percentil 95 dos RMS, em dB); ``None`` sem fala audível."""
    janela = janela_em_amostras(taxa_hz, cfg.pausas.janela_ms)
    niveis = []
    for seg in segmentos:
        a, b = _amostras(seg, taxa_hz, convertido.size)
        if b - a >= janela:
            niveis.append(para_db(rms_por_quadro(convertido[a:b], janela)))
    if not niveis:
        return None
    nivel = float(np.percentile(np.concatenate(niveis), 95))
    # O limiar das pausas é relativo a este nível; sem o piso absoluto, silêncio ou ruído de
    # ambiente seriam "a fala" de si mesmos.
    return nivel if nivel >= cfg.pausas.nivel_minimo_fala_db else None


def _amostras(seg: SegmentoFala, taxa_hz: int, total: int) -> tuple[int, int]:
    a = min(max(round(seg.inicio_ms * taxa_hz / 1000), 0), total)
    b = min(max(round(seg.fim_ms * taxa_hz / 1000), a), total)
    return a, b


def medir_segmentos(
    convertido: np.ndarray,
    copia: CopiaFeatures,
    segmentos: Sequence[SegmentoFala],
    cfg: ConfigVocal,
) -> list[MedidaSegmento]:
    """Mede cada segmento do paciente. Segmentos curtos ou sem fala audível são descartados."""
    taxa_hz = copia.taxa_hz
    nivel = nivel_de_fala_db(convertido, taxa_hz, segmentos, cfg)
    if nivel is None:
        return []
    janela = janela_em_amostras(taxa_hz, cfg.pausas.janela_ms)
    janela_s = janela / taxa_hz
    pausa_minima = max(1, math.ceil(cfg.pausas.duracao_minima_s / janela_s))
    limiar_db = nivel + cfg.pausas.limiar_relativo_db

    medidas: list[MedidaSegmento] = []
    fim_anterior_s: float | None = None
    indice_anterior: int | None = None
    for seg in segmentos:
        a, b = _amostras(seg, taxa_hz, convertido.size)
        if (b - a) / taxa_hz < cfg.segmentos.duracao_minima_s:
            continue
        db = para_db(rms_por_quadro(convertido[a:b], janela))
        fala = db >= limiar_db
        if not fala.any():
            continue
        primeiro, ultimo = int(np.argmax(fala)), int(fala.size - 1 - np.argmax(fala[::-1]))
        ini, fim = a + primeiro * janela, min(a + (ultimo + 1) * janela, b)
        inicio_s, fim_s = ini / taxa_hz, fim / taxa_hz
        if fim_s - inicio_s < cfg.segmentos.duracao_minima_s:
            continue

        interiores = [(i, f) for i, f in trechos(~fala[primeiro : ultimo + 1])
                      if f - i >= pausa_minima]
        pausas_s = sum((f - i) * janela_s for i, f in interiores)

        lacuna = pausa = 0.0
        if seg.contiguo_ao_anterior and fim_anterior_s is not None and (
            indice_anterior == seg.indice - 1
        ):
            lacuna = max(0.0, inicio_s - fim_anterior_s)
            pausa = lacuna if lacuna >= cfg.pausas.duracao_minima_s else 0.0

        f0, vozeado_s, jitter, shimmer, hnr = _praat_voz(
            copia.recortar(inicio_s, fim_s), taxa_hz, cfg)
        medidas.append(MedidaSegmento(
            indice=seg.indice, inicio_s=inicio_s, fim_s=fim_s, palavras=seg.palavras,
            pausas_s=pausas_s, n_pausas=len(interiores) + (1 if pausa else 0),
            lacuna_antes_s=lacuna, pausa_antes_s=pausa, vozeado_s=vozeado_s, f0_hz=f0,
            jitter_local_pct=jitter, shimmer_local_pct=shimmer, hnr_db=hnr,
            intensidade_db=_intensidade_db(convertido[ini:fim], taxa_hz, cfg),
        ))
        fim_anterior_s, indice_anterior = fim_s, seg.indice
    return medidas


def _media_ponderada(pares: Sequence[tuple[float | None, float]]) -> float | None:
    validos = [(v, w) for v, w in pares if v is not None and w > 0]
    peso = sum(w for _, w in validos)
    return None if peso <= 0 else sum(v * w for v, w in validos) / peso


def agregar(medidas: Sequence[MedidaSegmento]) -> BiomarcadoresAudio:
    """Junta os segmentos: tempos somados, jitter/shimmer/HNR ponderados pela voz sustentada."""
    if not medidas:
        raise SemFalaDoPaciente(
            "nenhum segmento de fala do paciente com duração e energia suficientes para medir"
        )
    duracao_fala = sum(m.duracao_s + m.lacuna_antes_s for m in medidas)
    pausa_total = sum(m.pausas_s + m.pausa_antes_s for m in medidas)
    n_pausas = sum(m.n_pausas for m in medidas)
    contagens = [m.palavras for m in medidas]
    palavras = None if any(c is None for c in contagens) else int(sum(contagens))  # type: ignore[arg-type]
    falando = duracao_fala - pausa_total
    f0 = np.concatenate([m.f0_hz for m in medidas])
    return BiomarcadoresAudio(
        segmentos=len(medidas),
        inicio_s=min(m.inicio_s for m in medidas),
        fim_s=max(m.fim_s for m in medidas),
        palavras=palavras,
        duracao_fala_s=duracao_fala,
        vozeado_s=sum(m.vozeado_s for m in medidas),
        pausa_total_s=pausa_total,
        n_pausas=n_pausas,
        pausa_media_s=pausa_total / n_pausas if n_pausas else 0.0,
        proporcao_pausas=pausa_total / duracao_fala if duracao_fala > 0 else 0.0,
        taxa_fala_pps=(
            palavras / duracao_fala if palavras is not None and duracao_fala > 0 else None),
        taxa_articulacao_pps=palavras / falando if palavras is not None and falando > 0 else None,
        f0_media_hz=float(f0.mean()) if f0.size else None,
        f0_desvio_st=_desvio_st(f0),
        jitter_local_pct=_media_ponderada([(m.jitter_local_pct, m.vozeado_s) for m in medidas]),
        shimmer_local_pct=_media_ponderada([(m.shimmer_local_pct, m.vozeado_s) for m in medidas]),
        hnr_db=_media_ponderada([(m.hnr_db, m.vozeado_s) for m in medidas]),
        intensidade_db=_media_ponderada([(m.intensidade_db, m.duracao_s) for m in medidas]),
    )
