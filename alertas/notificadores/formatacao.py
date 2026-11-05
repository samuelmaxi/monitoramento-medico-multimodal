"""Texto dos alertas. Usa só campos do contrato, então vale para qualquer modalidade.

Não há nome de paciente: aparece o pseudônimo ``pt_...`` e o leito.
"""

from __future__ import annotations

import html

from contratos import Severidade

from ..modelos import Alerta
from .base import LigacaoAck

ROTULOS = {Severidade.ALTA: "ALTA", Severidade.MEDIA: "MÉDIA", Severidade.BAIXA: "BAIXA"}
CORES = {Severidade.ALTA: "#B00020", Severidade.MEDIA: "#B26A00", Severidade.BAIXA: "#1E5FAD"}
TAMANHO_ASSUNTO = 120


def assunto(alerta: Alerta) -> str:
    rotulo = ROTULOS[alerta.priority]
    prefixo = _prefixo_simulado(alerta)
    return f"{prefixo}[{rotulo}] {_curto(alerta.summary)} · leito {_leito(alerta)}"


def campos(alerta: Alerta) -> list[tuple[str, str]]:
    return [
        ("Severidade", ROTULOS[alerta.priority]),
        ("Paciente (pseudônimo)", alerta.patient_id),
        ("Leito", _leito(alerta)),
        ("Modalidade", ", ".join(alerta.modalities)),
        ("Anomalia", alerta.event_type),
        ("Evidência", alerta.summary),
        ("Valores observados", _pares(alerta.evidence.get("features"))),
        ("Limiares aplicados", _pares(alerta.evidence.get("thresholds"))),
        ("Horário do evento", alerta.event_at.isoformat(timespec="seconds")),
        ("Ocorrências agregadas", str(alerta.occurrences)),
        ("Ação sugerida", alerta.suggested_action),
        ("Identificador do alerta", str(alerta.id)),
    ]


def corpo_texto(alerta: Alerta, ligacao: LigacaoAck | None = None) -> str:
    linhas = [f"{rotulo}: {valor}" for rotulo, valor in campos(alerta)]
    if ligacao is not None:
        linhas += ["", f"Reconhecer o alerta: {ligacao(alerta)}"]
    return "\n".join(linhas)


def corpo_html(alerta: Alerta, ligacao: LigacaoAck | None = None) -> str:
    cor = CORES[alerta.priority]
    linhas = "".join(
        f"<tr><th align='left'>{html.escape(rotulo)}</th><td>{html.escape(valor)}</td></tr>"
        for rotulo, valor in campos(alerta)
    )
    botao = ""
    if ligacao is not None:
        link = html.escape(ligacao(alerta), quote=True)
        botao = f"<p><a href='{link}'>Reconhecer o alerta</a></p>"
    return (
        "<html><body>"
        f"<h2 style='color:{cor}'>{html.escape(assunto(alerta))}</h2>"
        f"<table cellpadding='4'>{linhas}</table>{botao}"
        "</body></html>"
    )


def resumo_curto(alerta: Alerta, ligacao: LigacaoAck | None = None) -> str:
    """Uma linha para canais de chat (webhook de Teams ou Slack)."""
    texto = (
        f"{_prefixo_simulado(alerta)}[{ROTULOS[alerta.priority]}] {alerta.summary} "
        f"· leito {_leito(alerta)} · ação: {alerta.suggested_action}"
    )
    if ligacao is not None:
        texto += f"\nReconhecer: {ligacao(alerta)}"
    return texto


def _prefixo_simulado(alerta: Alerta) -> str:
    return "[SIMULADO] " if alerta.is_synthetic else ""


def _leito(alerta: Alerta) -> str:
    return alerta.bed_id or "não informado"


def _curto(texto: str) -> str:
    return texto if len(texto) <= TAMANHO_ASSUNTO else texto[: TAMANHO_ASSUNTO - 1] + "…"


def _pares(dados: dict | None) -> str:
    if not dados:
        return "—"
    return ", ".join(f"{chave}={valor}" for chave, valor in dados.items())
