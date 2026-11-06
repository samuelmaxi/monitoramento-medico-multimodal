"""Cenários de demonstração da US18, reutilizados pelos testes e por ``scripts/demo_us18.py``.

Os achados seguem exatamente o que cada módulo publica (mesmos ``event_type``,
severidades e ``features``), mas são marcados ``is_synthetic=True``: são casos
construídos para a demonstração, não saída de um detector sobre dado real.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from contratos import EventoAchado, Modalidade, Severidade

VERSAO = "cenario-us18@0.1.0"


def _evento(
    paciente: str,
    modalidade: Modalidade,
    tipo: str,
    instante: datetime,
    *,
    score: float,
    severidade: Severidade,
    resumo: str,
    features: dict | None = None,
    leito: str = "UTI-07",
    duracao: timedelta | None = None,
) -> EventoAchado:
    campos: dict = {
        "patient_id": paciente,
        "modality": modalidade,
        "event_type": tipo,
        "timestamp": instante,
        "detected_at": instante + timedelta(seconds=2),
        "ingested_at": instante,
        "score": score,
        "severity": severidade,
        "evidence": {"summary": resumo, "features": features or {}},
        "model_version": VERSAO,
        "context": {"bed_id": leito, "source_id": f"demo-{modalidade.value}"},
        "is_synthetic": True,
    }
    if duracao is not None:
        campos["window"] = {"start": instante, "end": instante + duracao}
    return EventoAchado.criar(**campos)


def cenario_dispneia(paciente: str, t0: datetime) -> list[EventoAchado]:
    """SpO2 em queda + fala com dispneia + relato de falta de ar. Sem vídeo e sem movimentação.

    Cada achado sozinho é no máximo risco médio; juntos, no eixo respiratório,
    passam a alto. Ordem cronológica, como chegariam no barramento.
    """
    return [
        _evento(
            paciente, Modalidade.PRESCRICOES, "sem_achados", t0,
            score=0.0, severidade=Severidade.INFO,
            resumo="Evolução de prescrições sem anomalias na internação",
        ),
        _evento(
            paciente, Modalidade.SINAIS_VITAIS, "sem_achados", t0 + timedelta(minutes=10),
            score=0.0, severidade=Severidade.INFO,
            resumo="NEWS2 = 2: janela sem anomalias",
            features={"spo2_pct": 95, "fr_irpm": 19, "news2_total": 2},
        ),
        _evento(
            paciente, Modalidade.SINAIS_VITAIS, "news2_resposta_urgente",
            t0 + timedelta(minutes=40),
            score=0.25, severidade=Severidade.MEDIA,
            resumo="NEWS2 = 5 (spo2 92, fr 22): resposta urgente",
            features={"spo2_pct": 92, "fr_irpm": 22, "news2_spo2": 2, "news2_fr": 2,
                      "news2_total": 5},
        ),
        _evento(
            paciente, Modalidade.AUDIO, "dificuldade_respiratoria", t0 + timedelta(minutes=45),
            score=0.62, severidade=Severidade.MEDIA,
            resumo="Fala entrecortada: pausas respiratórias frequentes e frases curtas",
            features={"pausas_por_min": 14.0, "duracao_media_frase_s": 1.9},
            duracao=timedelta(minutes=3),
        ),
        _evento(
            paciente, Modalidade.TEXTO, "termo_critico", t0 + timedelta(minutes=46),
            score=0.97, severidade=Severidade.MEDIA,
            resumo="Paciente relata falta de ar: “cansado, falta o ar” (afirmado, sem negação)",
            features={"termo": "falta de ar", "certeza": "positive"},
        ),
    ]


def cenario_sem_video(paciente: str, t0: datetime) -> list[EventoAchado]:
    """Só sinais vitais e prescrições, sem vídeo, áudio, texto ou movimentação.

    NEWS2 de emergência sozinho dá risco alto, mas a fusão não alerta de novo:
    o próprio achado já gerou o alerta (``alertar_com_uma_modalidade: false``).
    """
    return [
        _evento(
            paciente, Modalidade.PRESCRICOES, "sem_achados", t0,
            score=0.0, severidade=Severidade.INFO, leito="UTI-03",
            resumo="Evolução de prescrições sem anomalias na internação",
        ),
        _evento(
            paciente, Modalidade.SINAIS_VITAIS, "news2_emergencia", t0 + timedelta(minutes=30),
            score=0.4, severidade=Severidade.ALTA, leito="UTI-03",
            resumo="NEWS2 = 8 (spo2 89, fc 128): emergência",
            features={"spo2_pct": 89, "fc_bpm": 128, "news2_total": 8},
        ),
    ]
