"""Montagem do ``risco_multimodal`` no contrato ``EventoAchado`` (US04).

============================  =================================================
Contrato                      Fusão
============================  =================================================
``timestamp``                 ``ate``: fim da janela alinhada
``window``                    ``[ate - janela_min, ate]``
``score`` / ``severity``      risco 0–1 e nível (baixo → baixa, médio → media,
                              alto → alta; ``info`` sem achado ou com uma
                              modalidade só, ver ``emissao`` no YAML)
``evidence.features``         ``contrib_<modalidade>`` (soma = score),
                              ``forca_<modalidade>`` (risco isolado), cobertura
``evidence.thresholds``       parâmetros do YAML, com a versão
``related_event_ids``         achados usados, os que contribuíram primeiro
``ingested_at``               o mais recente dos achados usados (latência US19)
============================  =================================================
"""

from __future__ import annotations

from contratos import EventoAchado, Modalidade

from . import MODEL_VERSION
from .config import MODALIDADES_FUNDIDAS, ConfigFusao
from .score import Risco

RISCO_MULTIMODAL = "risco_multimodal"
MAX_RELACIONADOS = 200  # limite de related_event_ids no contrato
MAX_RESUMO = 500

ROTULO = {
    Modalidade.SINAIS_VITAIS: "sinais vitais",
    Modalidade.AUDIO: "áudio",
    Modalidade.TEXTO: "texto",
    Modalidade.VIDEO: "vídeo",
    Modalidade.PRESCRICOES: "prescrições",
    Modalidade.MOVIMENTACAO: "movimentação",
}
ROTULO_NIVEL = {"baixo": "baixo", "medio": "médio", "alto": "alto"}
ROTULO_EIXO = {"respiratorio": "respiratório", "neurologico": "neurológico"}


def _br(valor: float) -> str:
    return f"{valor:.2f}".replace(".", ",")


def _tipo(evento: EventoAchado) -> str:
    return evento.event_type.replace("_", " ")


def resumo(risco: Risco) -> str:
    """Frase para a equipe: nível, maiores contribuições, concordância e lacunas."""
    partes = [f"Risco {ROTULO_NIVEL[risco.nivel]} ({_br(risco.score)})"]
    contribuintes = sorted(
        (s for s in risco.sinais.values() if s.contribuicao > 0),
        key=lambda s: s.contribuicao, reverse=True,
    )
    if contribuintes:
        itens = [
            f"{ROTULO[s.modalidade]} {_br(s.contribuicao)} ({_tipo(s.evento)})"
            for s in contribuintes if s.evento is not None
        ]
        partes.append("contribuições: " + ", ".join(itens))
    else:
        partes.append("nenhuma modalidade com achado na janela")
    if risco.concordancia is not None:
        mods = " + ".join(ROTULO[m] for m in risco.concordancia.modalidades)
        eixo = ROTULO_EIXO.get(risco.concordancia.eixo, risco.concordancia.eixo)
        partes.append(f"concordância no eixo {eixo} ({mods})")
    if risco.ausentes:
        partes.append("sem dados de " + ", ".join(ROTULO[m] for m in risco.ausentes))
    texto = "; ".join(partes)
    return texto if len(texto) <= MAX_RESUMO else texto[: MAX_RESUMO - 1].rstrip() + "…"


def features(risco: Risco) -> dict[str, float | int | str | None]:
    dados: dict[str, float | int | str | None] = {"nivel": risco.nivel}
    for m in MODALIDADES_FUNDIDAS:
        dados[f"contrib_{m.value}"] = round(risco.sinais[m].contribuicao, 4)
    dados["contrib_concordancia"] = round(
        risco.concordancia.contribuicao if risco.concordancia else 0.0, 4
    )
    for m in MODALIDADES_FUNDIDAS:
        dados[f"forca_{m.value}"] = round(risco.sinais[m].forca, 4)
    dados |= {
        "eixo_concordancia": risco.concordancia.eixo if risco.concordancia else None,
        "score_max_isolado": round(risco.score_max_isolado, 4),
        "ganho_fusao": round(risco.ganho_fusao, 4),
        "modalidades_presentes": len(risco.presentes),
        "modalidades_com_achado": len(risco.com_achado),
        "modalidades_ausentes": ",".join(m.value for m in risco.ausentes),
        "eventos_considerados": len(risco.eventos),
    }
    return dados


def _relacionados(risco: Risco) -> list:
    """Achados que definiram a força de cada modalidade primeiro, depois os demais."""
    principais = [s.evento.event_id for s in risco.sinais.values() if s.evento is not None]
    restantes = [e.event_id for e in risco.eventos if e.event_id not in set(principais)]
    return (principais + restantes)[:MAX_RELACIONADOS]


def _contexto(risco: Risco) -> dict | None:
    """Leito e internação do achado mais recente que os informa."""
    for evento in sorted(risco.eventos, key=lambda e: e.timestamp, reverse=True):
        ctx = evento.context
        if ctx and (ctx.bed_id or ctx.encounter_id):
            return {
                k: v for k, v in {
                    "bed_id": ctx.bed_id, "encounter_id": ctx.encounter_id, "source_id": "fusao",
                }.items() if v
            }
    return {"source_id": "fusao"}


def evento_de_risco(risco: Risco, config: ConfigFusao) -> EventoAchado:
    """``risco_multimodal`` pronto para o barramento. Exige ao menos um achado alinhado."""
    if not risco.eventos:
        raise ValueError("risco sem achados alinhados: a fusão não emite evento vazio")
    ingestoes = [e.ingested_at for e in risco.eventos if e.ingested_at is not None]
    return EventoAchado.criar(
        patient_id=risco.patient_id,
        modality=Modalidade.FUSAO,
        event_type=RISCO_MULTIMODAL,
        timestamp=risco.ate,
        ingested_at=max(ingestoes) if ingestoes else None,
        window={"start": risco.inicio, "end": risco.ate},
        score=round(risco.score, 4),
        severity=risco.severidade,
        evidence={
            "summary": resumo(risco),
            "features": features(risco),
            "thresholds": config.limiares(),
            "models": {"fusao": "late-fusion noisy-OR ponderada + concordância clínica"},
        },
        model_version=MODEL_VERSION,
        context=_contexto(risco),
        related_event_ids=_relacionados(risco),
        is_synthetic=any(e.is_synthetic for e in risco.eventos),
    )
