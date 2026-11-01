"""Painel HTML dos alertas (US19): visão da equipe sobre o que foi gerado, enviado e reconhecido.

Funções puras sobre a lista de ``Alerta``. O roteador só busca no armazenamento e chama
``renderizar_painel``. Todo texto vem escapado. O painel não mostra o link de
reconhecimento, que exige o token HMAC enviado no e-mail ou no webhook.
"""

from __future__ import annotations

import html
from collections import Counter
from datetime import datetime

from contratos import Severidade

from .modelos import Alerta, StatusAlerta
from .notificadores.formatacao import CORES, ROTULOS

ROTULOS_STATUS = {
    StatusAlerta.PENDENTE: "pendente",
    StatusAlerta.ENVIADO: "enviado",
    StatusAlerta.FALHOU: "falhou",
    StatusAlerta.REGISTRADO: "só registrado",
    StatusAlerta.RECONHECIDO: "reconhecido",
}
ORDEM_PRIORIDADE = (Severidade.ALTA, Severidade.MEDIA, Severidade.BAIXA)
INTERVALO_ATUALIZACAO_S = 10


def contagens(alertas: list[Alerta]) -> dict:
    por_prioridade = Counter(a.priority for a in alertas)
    por_status = Counter(a.status for a in alertas)
    return {
        "total": len(alertas),
        "nao_reconhecidos": sum(1 for a in alertas if a.status is not StatusAlerta.RECONHECIDO),
        "por_prioridade": {p.value: por_prioridade.get(p, 0) for p in ORDEM_PRIORIDADE},
        "por_status": {s.value: por_status.get(s, 0) for s in StatusAlerta},
    }


def renderizar_painel(
    alertas: list[Alerta],
    latencia: dict,
    *,
    status: str | None = None,
    prioridade: str | None = None,
    agora: datetime | None = None,
) -> str:
    resumo = contagens(alertas)
    cartoes = "".join(
        _cartao(ROTULOS[p], resumo["por_prioridade"][p.value], CORES[p]) for p in ORDEM_PRIORIDADE
    )
    cartoes += _cartao("Não reconhecidos", resumo["nao_reconhecidos"], "#444")
    cartoes += _cartao("Total listado", resumo["total"], "#444")

    linhas = "".join(_linha(a) for a in alertas) or (
        "<tr><td colspan='11'>Nenhum alerta para os filtros escolhidos.</td></tr>"
    )
    filtros = html.escape(
        ", ".join(f"{k}={v}" for k, v in (("status", status), ("prioridade", prioridade)) if v)
        or "nenhum"
    )
    carimbo = html.escape(agora.isoformat(timespec="seconds") if agora else "—")
    return (
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        f"<meta http-equiv='refresh' content='{INTERVALO_ATUALIZACAO_S}'>"
        "<title>Alertas · Monitoramento médico</title>"
        f"<style>{_CSS}</style></head><body>"
        "<h1>Alertas para a equipe médica</h1>"
        f"<p class='sub'>Atualiza a cada {INTERVALO_ATUALIZACAO_S} s · filtros: {filtros} · "
        f"gerado em {carimbo}</p>"
        f"<div class='cartoes'>{cartoes}</div>"
        f"{_bloco_latencia(latencia)}"
        "<div class='rolagem'><table><thead><tr>"
        "<th>Criado em</th><th>Prioridade</th><th>Paciente (pseudônimo)</th><th>Leito</th>"
        "<th>Modalidade</th><th>Anomalia / evidência</th><th>Ação sugerida</th>"
        "<th>Status</th><th>Ocorrências</th><th>Reconhecimento</th><th>Auditoria</th>"
        f"</tr></thead><tbody>{linhas}</tbody></table></div>"
        "</body></html>"
    )


def _cartao(titulo: str, valor: int, cor: str) -> str:
    return (
        f"<div class='cartao' style='border-top:4px solid {cor}'>"
        f"<div class='valor'>{valor}</div><div>{html.escape(titulo)}</div></div>"
    )


def _bloco_latencia(latencia: dict) -> str:
    if not latencia.get("amostras"):
        return "<p class='lat'>Latência ingestão → envio: sem alertas enviados ainda.</p>"
    classe = "ok" if latencia["atendida"] else "ruim"
    veredito = "meta atendida" if latencia["atendida"] else "meta NÃO atendida"
    return (
        f"<p class='lat {classe}'>Latência ingestão → envio ({latencia['amostras']} alertas): "
        f"p50 {latencia['p50_s']} s · p95 {latencia['p95_s']} s · máx {latencia['max_s']} s · "
        f"meta p95 ≤ {latencia['meta_p95_s']:g} s: {veredito}</p>"
    )


def _linha(a: Alerta) -> str:
    cor = CORES[a.priority]
    simulado = " <em>(simulado)</em>" if a.is_synthetic else ""
    reconhecimento = (
        f"por {html.escape(a.acked_by or '—')} em "
        f"{html.escape(a.acked_at.isoformat(timespec='seconds')) if a.acked_at else '—'}"
        if a.status is StatusAlerta.RECONHECIDO
        else "<strong>aguardando</strong>"
    )
    escalonamento = f" · escalonado {a.escalation_level}x" if a.escalation_level else ""
    return (
        "<tr>"
        f"<td>{html.escape(a.alert_created_at.isoformat(timespec='seconds'))}</td>"
        f"<td><span class='tag' style='background:{cor}'>{html.escape(ROTULOS[a.priority])}</span>"
        f"{simulado}</td>"
        f"<td><code>{html.escape(a.patient_id)}</code></td>"
        f"<td>{html.escape(a.bed_id or 'não informado')}</td>"
        f"<td>{html.escape(', '.join(a.modalities))}</td>"
        f"<td><strong>{html.escape(a.event_type)}</strong><br>{html.escape(a.summary)}</td>"
        f"<td>{html.escape(a.suggested_action)}</td>"
        f"<td>{html.escape(ROTULOS_STATUS[a.status])}</td>"
        f"<td>{a.occurrences}{html.escape(escalonamento)}</td>"
        f"<td>{reconhecimento}</td>"
        f"<td><a href='/alerts/{html.escape(str(a.id), quote=True)}'>trilha</a></td>"
        "</tr>"
    )


_CSS = """
:root{color-scheme:light dark;--bg:#fff;--fg:#1c1c1c;--card:#f4f4f6;--borda:#d0d0d6}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#ececec;--card:#232327;--borda:#3a3a40}}
body{font-family:system-ui,sans-serif;margin:1.2rem;background:var(--bg);color:var(--fg)}
h1{margin:0 0 .2rem}.sub{margin:0 0 1rem;opacity:.7}
.cartoes{display:flex;gap:.8rem;flex-wrap:wrap;margin-bottom:1rem}
.cartao{background:var(--card);padding:.6rem 1rem;min-width:8rem;border-radius:6px}
.valor{font-size:1.8rem;font-weight:700}
.lat{padding:.5rem .8rem;border-radius:6px;background:var(--card)}
.lat.ok{border-left:4px solid #1b8a3a}.lat.ruim{border-left:4px solid #b00020}
.rolagem{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{border-bottom:1px solid var(--borda);padding:.4rem .5rem;text-align:left;vertical-align:top}
.tag{color:#fff;padding:.1rem .5rem;border-radius:4px;font-weight:700}
a{color:inherit}
"""
