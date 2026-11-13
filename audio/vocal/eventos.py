"""Tradução dos achados vocais para o contrato ``EventoAchado`` (US04), modalidade ``audio``.

Os seis ``event_type`` já existem no catálogo da modalidade, então nada muda no schema.

============================  ================================================================
Achado                        ``EventoAchado``
============================  ================================================================
classe disparada (US14)       ``fadiga_vocal``, ``dificuldade_respiratoria`` ou
                              ``possivel_disartria``
nenhuma classe e nada mais    ``sem_achados`` (info): é a classe "normal"
trecho do YAMNet (US12)       ``evento_respiratorio`` com o ``score`` do trecho
métricas fora da baseline     ``desvio_baseline_vocal``
critérios, valores, limiares  ``evidence.features``, ``.thresholds`` e ``.summary``
trecho de fala analisado      ``window`` e artefato ``audio_segment`` (``convertido.wav``)
============================  ================================================================

Todo ``summary`` começa com o aviso de que o resultado é **indicativo para avaliação clínica,
não diagnóstico**, e o aviso também vai em ``features.aviso`` (o e-mail de alerta lista as
features).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from audio.text.events import cortar
from contratos import SEM_ACHADOS, EventoAchado, Modalidade, Severidade

from . import MODEL_VERSION
from .baseline import ComparacaoBaseline
from .biomarcadores import PARSELMOUTH_VERSAO, PRAAT_VERSAO, BiomarcadoresAudio
from .classificacao import (
    AVISO,
    CLASSE_NORMAL,
    ClasseDetectada,
    Classificacao,
    limiares_da_classe,
)
from .config import ConfigVocal
from .respiratorio import NOME_DO_MODELO, EventoRespiratorio, ResumoRespiratorio

PREFIXO = "Indicativo para avaliação clínica, não é diagnóstico"
EVENTO_RESPIRATORIO = "evento_respiratorio"
DESVIO_BASELINE = "desvio_baseline_vocal"

ROTULOS_DA_CLASSE = {
    "fadiga_vocal": "fadiga vocal",
    "dificuldade_respiratoria": "dificuldade respiratória",
    "possivel_disartria": "possível disartria",
}
ROTULOS_RESPIRATORIOS = {
    "tosse": "tosse",
    "respiracao": "respiração audível",
    "sibilo": "sibilo",
    "arfar": "arfar",
    "respiracao_ofegante": "respiração ofegante",
}


@dataclass(frozen=True)
class ContextoEvento:
    audio_id: str
    patient_id: str
    """Pseudônimo (``pt_`` + 24 hex), já calculado."""
    recorded_at: datetime
    convertido_uri: str | None = None
    relatorio_uri: str | None = None
    bed_id: str | None = None
    encounter_id: str | None = None
    sintetico: bool = False
    fonte_segmentos: str = "vad"
    papel_assumido: bool = False
    formato_origem: str | None = None
    audio_comprimido: bool = False
    """Origem em codec com perdas: jitter, shimmer e HNR podem estar superestimados."""

    def instante(self, segundos: float) -> datetime:
        return self.recorded_at + timedelta(seconds=segundos)


def _f(valor: float | None, casas: int = 3) -> float | None:
    return None if valor is None else round(float(valor), casas)


def _modelos(ctx: ContextoEvento, resp: ResumoRespiratorio | None) -> dict[str, str]:
    modelos = {"praat": str(PRAAT_VERSAO), "parselmouth": str(PARSELMOUTH_VERSAO)}
    if resp is not None and resp.disponivel:
        modelos["yamnet"] = NOME_DO_MODELO
    if ctx.fonte_segmentos == "transcricao":
        modelos["azure-speech"] = "transcricao-rapida"
    return modelos


def _artefatos(
    ctx: ContextoEvento, inicio_s: float, fim_s: float
) -> list[dict[str, Any]]:
    artefatos: list[dict[str, Any]] = []
    if ctx.convertido_uri:
        artefatos.append({
            "kind": "audio_segment", "uri": ctx.convertido_uri, "media_type": "audio/wav",
            "start_ms": max(0, round(inicio_s * 1000)), "end_ms": max(0, round(fim_s * 1000)),
        })
    if ctx.relatorio_uri:
        artefatos.append({
            "kind": "relatorio", "uri": ctx.relatorio_uri, "media_type": "application/json"})
    return artefatos


def _base(ctx: ContextoEvento, event_type: str, inicio_s: float, fim_s: float) -> dict[str, Any]:
    contexto: dict[str, str] = {"source_id": ctx.audio_id}
    if ctx.bed_id:
        contexto["bed_id"] = ctx.bed_id
    if ctx.encounter_id:
        contexto["encounter_id"] = ctx.encounter_id
    return {
        "patient_id": ctx.patient_id,
        "modality": Modalidade.AUDIO,
        "event_type": event_type,
        "timestamp": ctx.instante(inicio_s),
        "window": {"start": ctx.instante(inicio_s), "end": ctx.instante(max(fim_s, inicio_s))},
        "model_version": MODEL_VERSION,
        "context": contexto,
        "is_synthetic": ctx.sintetico,
    }


def _medidas(bio: BiomarcadoresAudio) -> dict[str, Any]:
    """As medições do áudio, com os nomes que o alerta mostra ao profissional."""
    return {
        "jitter_local_pct": _f(bio.jitter_local_pct),
        "shimmer_local_pct": _f(bio.shimmer_local_pct),
        "hnr_db": _f(bio.hnr_db, 2),
        "f0_media_hz": _f(bio.f0_media_hz, 1),
        "f0_desvio_st": _f(bio.f0_desvio_st, 2),
        "intensidade_db": _f(bio.intensidade_db, 1),
        "proporcao_pausas": _f(bio.proporcao_pausas),
        "pausa_media_s": _f(bio.pausa_media_s),
        "pausas_n": int(bio.n_pausas),
        "taxa_fala_pps": _f(bio.taxa_fala_pps),
        "taxa_articulacao_pps": _f(bio.taxa_articulacao_pps),
        "palavras": bio.palavras,
        "duracao_fala_s": _f(bio.duracao_fala_s, 2),
        "segmentos": int(bio.segmentos),
    }


def _contexto_da_analise(
    ctx: ContextoEvento, resp: ResumoRespiratorio | None, comparacao: ComparacaoBaseline | None
) -> dict[str, Any]:
    return {
        "aviso": AVISO,
        "fonte_segmentos": ctx.fonte_segmentos,
        "papel_assumido": ctx.papel_assumido,
        "formato_origem": ctx.formato_origem,
        "audio_comprimido": ctx.audio_comprimido,
        "eventos_respiratorios": len(resp.eventos) if resp and resp.disponivel else None,
        "yamnet_score_max": _f(resp.score_max) if resp and resp.disponivel else None,
        "baseline_audios": comparacao.audios if comparacao else 0,
    }


def montar_evento_classe(
    classe: ClasseDetectada,
    bio: BiomarcadoresAudio,
    resp: ResumoRespiratorio | None,
    comparacao: ComparacaoBaseline | None,
    cfg: ConfigVocal,
    ctx: ContextoEvento,
) -> EventoAchado:
    regra = cfg.regras[classe.event_type]
    rotulo = ROTULOS_DA_CLASSE[classe.event_type]
    criterios = "; ".join(a.texto for a in classe.satisfeitos)
    resumo = (f"{PREFIXO}: padrão compatível com {rotulo} "
              f"({len(classe.satisfeitos)} de {classe.total_criterios} critérios): {criterios}")
    return EventoAchado.criar(
        **_base(ctx, classe.event_type, bio.inicio_s, bio.fim_s),
        score=classe.score,
        severity=classe.severidade,
        evidence={
            "summary": cortar(resumo, 500),
            "features": {
                **_contexto_da_analise(ctx, resp, comparacao),
                "regras_acionadas": cortar(classe.regras_acionadas),
                "criterios_atendidos": len(classe.satisfeitos),
                "criterios_total": classe.total_criterios,
                "criterios_nao_avaliados": cortar(", ".join(classe.nao_avaliados)) or None,
                **_medidas(bio),
            },
            "thresholds": {
                "versao_limiares": cfg.versao,
                "minimo_criterios": regra.minimo,
                **({"criterios_forte": regra.forte} if regra.forte is not None else {}),
                **limiares_da_classe(regra, cfg.limiares),
            },
            "models": _modelos(ctx, resp),
            "artifacts": _artefatos(ctx, bio.inicio_s, bio.fim_s),
        },
    )


def montar_evento_respiratorio(
    evento: EventoRespiratorio, cfg: ConfigVocal, ctx: ContextoEvento
) -> EventoAchado:
    rotulo = ROTULOS_RESPIRATORIOS.get(evento.tipo, evento.tipo)
    return EventoAchado.criar(
        **_base(ctx, EVENTO_RESPIRATORIO, evento.inicio_s, evento.fim_s),
        score=round(min(max(evento.score_max, 0.0), 1.0), 4),
        severity=evento.severidade,
        evidence={
            "summary": (f"{PREFIXO}: {rotulo} detectada(o) entre {evento.inicio_s:.1f} s e "
                        f"{evento.fim_s:.1f} s do áudio (score {evento.score_max:.2f})"),
            "features": {
                "aviso": AVISO,
                "tipo": evento.tipo,
                "classe_audioset": evento.classe_audioset,
                "score_max": _f(evento.score_max, 4),
                "score_medio": _f(evento.score_medio, 4),
                "duracao_s": _f(evento.fim_s - evento.inicio_s, 2),
                "quadros": evento.quadros,
            },
            "thresholds": {
                "versao_limiares": cfg.versao,
                "score_minimo": cfg.respiratorio.score_minimo,
            },
            "models": {"yamnet": NOME_DO_MODELO},
            "artifacts": _artefatos(ctx, evento.inicio_s, evento.fim_s),
        },
    )


def montar_evento_baseline(
    comparacao: ComparacaoBaseline,
    bio: BiomarcadoresAudio,
    resp: ResumoRespiratorio | None,
    cfg: ConfigVocal,
    ctx: ContextoEvento,
) -> EventoAchado:
    b = cfg.baseline
    fortes = len(comparacao.desvios) >= b.metricas_fortes
    maior_z = max(abs(d.z) for d in comparacao.desvios)
    trechos = [f"{d.metrica} {d.valor:.2f} ({d.direcao} da mediana {d.mediana:.2f}, z={d.z:+.1f})"
               for d in comparacao.desvios[:4]]
    features: dict[str, Any] = {
        **_contexto_da_analise(ctx, resp, comparacao),
        "metricas_fora_da_baseline": cortar(", ".join(d.metrica for d in comparacao.desvios)),
        "n_metricas_fora": len(comparacao.desvios),
        "z_maximo": _f(maior_z, 2),
    }
    for d in comparacao.desvios:
        features[d.metrica] = _f(d.valor, 4)
        features[f"{d.metrica}_mediana"] = _f(d.mediana, 4)
        features[f"{d.metrica}_z"] = _f(d.z, 2)
    return EventoAchado.criar(
        **_base(ctx, DESVIO_BASELINE, bio.inicio_s, bio.fim_s),
        score=round(min(1.0, maior_z / (2 * b.z_minimo)), 4),
        severity=b.severidade_forte if fortes else b.severidade,
        evidence={
            "summary": cortar(
                f"{PREFIXO}: {len(comparacao.desvios)} biomarcador(es) fora da baseline do próprio "
                f"paciente ({comparacao.audios} áudios anteriores): " + "; ".join(trechos), 500),
            "features": features,
            "thresholds": {
                "versao_limiares": cfg.versao,
                "z_minimo": b.z_minimo,
                "audios_minimos": b.audios_minimos,
                "metricas_fortes": b.metricas_fortes,
            },
            "models": _modelos(ctx, resp),
            "artifacts": _artefatos(ctx, bio.inicio_s, bio.fim_s),
        },
    )


def montar_sem_achados(
    bio: BiomarcadoresAudio,
    resp: ResumoRespiratorio | None,
    comparacao: ComparacaoBaseline | None,
    cfg: ConfigVocal,
    ctx: ContextoEvento,
) -> EventoAchado:
    """``sem_achados`` (info): áudio analisado, nenhuma classe disparou. É a classe "normal"."""
    ressalva = "" if resp is not None and resp.disponivel else " (sem o detector respiratório)"
    return EventoAchado.criar(
        **_base(ctx, SEM_ACHADOS, bio.inicio_s, bio.fim_s),
        score=0.0,
        severity=Severidade.INFO,
        evidence={
            "summary": (f"{PREFIXO}: nenhuma alteração vocal nos critérios avaliados{ressalva} "
                        f"(classe: {CLASSE_NORMAL})"),
            "features": {**_contexto_da_analise(ctx, resp, comparacao), **_medidas(bio)},
            "thresholds": {"versao_limiares": cfg.versao},
            "models": _modelos(ctx, resp),
            "artifacts": _artefatos(ctx, bio.inicio_s, bio.fim_s),
        },
    )


def montar_eventos(
    bio: BiomarcadoresAudio,
    resp: ResumoRespiratorio | None,
    comparacao: ComparacaoBaseline | None,
    classificacao: Classificacao,
    cfg: ConfigVocal,
    ctx: ContextoEvento,
) -> list[EventoAchado]:
    """Todos os eventos do áudio, do mais grave para o menos. Nada achado = um ``sem_achados``."""
    eventos = [
        montar_evento_classe(c, bio, resp, comparacao, cfg, ctx) for c in classificacao.classes
    ]
    if resp is not None:
        eventos += [montar_evento_respiratorio(e, cfg, ctx) for e in resp.eventos]
    if comparacao is not None and comparacao.desvios:
        eventos.append(montar_evento_baseline(comparacao, bio, resp, cfg, ctx))
    if not eventos:
        eventos.append(montar_sem_achados(bio, resp, comparacao, cfg, ctx))
    return sorted(eventos, key=lambda e: (-e.severity.peso, e.timestamp))


def relatorio(
    eventos: list[EventoAchado],
    bio: BiomarcadoresAudio,
    medidas: list[dict[str, Any]],
    resp: ResumoRespiratorio | None,
    comparacao: ComparacaoBaseline | None,
    classificacao: Classificacao,
    cfg: ConfigVocal,
    ctx: ContextoEvento,
    notas: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Resumo da análise de um áudio: o que foi medido, o que disparou e por quê."""
    return {
        "audio_id": ctx.audio_id,
        "patient_id": ctx.patient_id,
        "model_version": MODEL_VERSION,
        "versao_limiares": cfg.versao,
        "config": str(cfg.fonte) if cfg.fonte else None,
        "aviso": AVISO,
        "classe": classificacao.classe_principal,
        "classes": [c.event_type for c in classificacao.classes] or [CLASSE_NORMAL],
        "fonte_segmentos": ctx.fonte_segmentos,
        "papel_assumido": ctx.papel_assumido,
        "notas": list(notas),
        "biomarcadores": bio.para_dict(),
        "segmentos": medidas,
        "respiratorio": {
            "disponivel": bool(resp and resp.disponivel),
            "eventos": [e.para_dict() for e in resp.eventos] if resp else [],
        },
        "baseline": comparacao.para_dict() if comparacao else None,
        "criterios": [a.para_dict() for a in classificacao.avaliacoes.values()],
        "classificacao": [c.para_dict() for c in classificacao.classes],
        "eventos": [
            {"event_id": str(e.event_id), "event_type": e.event_type,
             "severity": e.severity.value, "score": e.score}
            for e in eventos
        ],
        "por_tipo": dict(Counter(e.event_type for e in eventos)),
        "alertaveis": sum(e.alertavel for e in eventos if e.event_type != SEM_ACHADOS),
    }
