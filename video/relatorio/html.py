"""Página HTML do relatório da US09: um arquivo só, sem script e sem recurso externo.

Abre offline em qualquer navegador e imprime em PDF por ele (``Ctrl+P``). Os quadros de
evidência vão embutidos (base64), então o arquivo pode ser enviado por e-mail sem as pastas.

Gráfico: uma linha do tempo com uma faixa por tipo de desvio. A **severidade** é ordinal
(baixa < média < alta), então vai em uma rampa de um só tom, clara → escura, validada com o
``validate_palette.js`` da skill de visualização (``--ordinal``, fundo branco): passa em
monotonia de luminosidade, passo entre tons, contraste do tom mais claro e tom único. Como a
rampa é monótona em luminosidade, continua legível em impressão em tons de cinza. A cor nunca
carrega o sentido sozinha: há legenda, o rótulo de cada desvio na tabela logo abaixo (a "visão
em tabela") e um ``<title>`` em cada barra.
"""

from __future__ import annotations

import base64
import html
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import cv2

from .config import ConfigEvidencia

log = logging.getLogger(__name__)

SEVERIDADE_ROTULO = {"alta": "Alta", "media": "Média", "baixa": "Baixa"}
# Rampa ordinal azul, passos 250 / 450 / 650 da paleta de referência (validada, fundo #fff).
COR_SEVERIDADE = {"baixa": "#86b6ef", "media": "#2a78d6", "alta": "#104281"}

_ROTULO_SECAO = {
    "faixas": "Faixas de ângulo (graus)",
    "assimetrias": "Assimetrias entre esquerda e direita (graus)",
    "repeticoes": "Repetições",
    "queda": "Queda (limiar: descida do quadril, em comprimentos de tronco)",
    "mudanca_brusca": "Mudança brusca (limiar: giro do tronco, em graus)",
    "areas": "Áreas críticas",
}

# Nomes dos campos da configuração como o leitor do relatório os entende, com a unidade.
_ROTULO_CHAVE = {
    "limiar": "Limiar",
    "tempo_max_s": "Tempo máximo do movimento (s)",
    "janela_s": "Persistência mínima (s)",
    "severidade": "Severidade",
    "area_id": "Área",
    "classes_proibidas": "Classes proibidas",
    "coluna": "Medida",
    "min": "Mínimo",
    "max": "Máximo",
    "angulo": "Ângulo principal",
    "sentido": "Sentido",
    "repouso": "Repouso (°)",
    "alvo": "Alvo (°)",
    "esperadas": "Repetições esperadas",
    "duracao_min_s": "Duração mínima (s)",
    "referencia": "Referência do paciente",
    "repeticoes": "Repetições de referência",
    "tolerancias": "Tolerância (± °)",
    "fase_pico_graus": "Fase de pico (± °)",
    "confianca_minima": "Confiança mínima da detecção",
    "iou_nms": "IoU da supressão de sobreposição",
    "criterio_roi": "Critério de pertencer à área",
    "persistencia_quadros": "Persistência (quadros)",
}

_CSS_SEVERIDADE = "".join(
    f".m-{s}{{fill:{cor}}}.sw-{s}{{background:{cor}}}" for s, cor in COR_SEVERIDADE.items()
)

_CSS = (
    """
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:#f9f9f7;color:#0b0b0b;
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1040px;margin:0 auto;padding:24px 20px 48px}
h1{font-size:24px;line-height:1.25;margin:0 0 4px}
h2{font-size:17px;margin:0 0 12px}
h3{font-size:14px;margin:16px 0 6px;color:#52514e}
.sub{color:#52514e;margin:0}
section{background:#fcfcfb;border:1px solid rgba(11,11,11,.10);border-radius:8px;
  padding:18px 20px;margin:16px 0}
.status{display:flex;gap:10px;align-items:baseline;border-radius:8px;padding:14px 18px;
  margin:16px 0;border:1px solid rgba(11,11,11,.10);border-left-width:6px;background:#fcfcfb;
  font-size:17px}
.status .icone{font-size:18px}
.status-ok{border-left-color:#0ca30c}
.status-aviso{border-left-color:#fab219}
.status-desvio{border-left-color:#104281}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;
  margin-bottom:14px}
.kpi{border:1px solid rgba(11,11,11,.10);border-radius:8px;padding:12px 14px;background:#fff}
.kpi-valor{font-size:30px;line-height:1.1;font-weight:600}
.kpi-rotulo{color:#52514e;font-size:13px;margin-top:2px}
table{border-collapse:collapse;width:100%}
caption{text-align:left;color:#52514e;font-size:13px;padding-bottom:6px}
th,td{padding:8px 10px;border-bottom:1px solid #e1e0d9;vertical-align:top;text-align:left}
th{color:#52514e;font-weight:600;font-size:13px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr{break-inside:avoid}
dl.grade{display:grid;grid-template-columns:max-content 1fr;gap:6px 18px;margin:0}
dl.grade dt{color:#52514e}
dl.grade dd{margin:0}
dl.aninhado{margin:0 0 0 14px}
code{font:13px ui-monospace,SFMono-Regular,Consolas,monospace;word-break:break-all}
.sev{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px;
  box-shadow:0 0 0 1px rgba(11,11,11,.25) inset}
.legenda{display:flex;gap:18px;list-style:none;padding:0;margin:8px 0 0;font-size:13px;
  color:#52514e}
.baixa-conf{color:#52514e;font-size:12px;display:block}
figure.ev{margin:0}
figure.ev img{display:block;width:100%;max-width:240px;height:auto;border-radius:4px;
  border:1px solid #e1e0d9}
figure.ev figcaption{font-size:12px;color:#52514e;margin-top:2px}
ul.avisos{margin:0;padding-left:20px}
.nota{color:#52514e;font-size:13px}
svg.linha-do-tempo{width:100%;height:auto;display:block}
svg text{font:12px system-ui,-apple-system,"Segoe UI",sans-serif;fill:#52514e}
svg .rotulo{fill:#0b0b0b;font-size:13px}
svg .grade{stroke:#e1e0d9;stroke-width:1}
svg .base{stroke:#c3c2b7;stroke-width:1}
svg rect.marca{stroke:#fff;stroke-width:2}
footer{color:#52514e;font-size:13px;margin-top:18px}
@media print{
  body{background:#fff}
  main{padding:0}
  section,.status{break-inside:avoid;border-color:#c3c2b7}
  figure.ev img{max-width:200px}
}
@page{margin:14mm}
"""
    + _CSS_SEVERIDADE
)


def _e(texto: Any) -> str:
    return html.escape("" if texto is None else str(texto), quote=True)


def _n(valor: float | int | None, casas: int = 1) -> str:
    """Número em formato brasileiro (vírgula decimal); ``—`` quando ausente."""
    if valor is None:
        return "—"
    return f"{valor:.{casas}f}".replace(".", ",")


def _g(valor: Any) -> str:
    """Valor de configuração sem zeros à direita (``30.0`` → ``30``)."""
    if valor is None:
        return "—"
    if isinstance(valor, bool):
        return "sim" if valor else "não"
    if isinstance(valor, float):
        return f"{valor:g}".replace(".", ",")
    return _e(valor)


def _s(segundos: float | None) -> str:
    return "—" if segundos is None else f"{_n(segundos, 1)} s"


def _pct(valor: float | None) -> str:
    return "—" if valor is None else f"{_n(valor, 1)}%"


def _sev(severidade: str) -> str:
    rotulo = SEVERIDADE_ROTULO.get(severidade, severidade)
    return (
        f'<span class="sev"><i class="sw sw-{_e(severidade)}" aria-hidden="true"></i>'
        f"{_e(rotulo)}</span>"
    )


# --------------------------------------------------------------------------- #
# Linha do tempo (SVG)
# --------------------------------------------------------------------------- #

_LARGURA, _ESQ, _DIR, _TOPO, _LINHA, _EIXO = 920, 250, 20, 6, 30, 30


def _linha_do_tempo(rel: Mapping[str, Any]) -> str:
    itens = rel["desvios"]
    if not itens:
        return ""
    duracao = rel["video"]["duracao_s"] or max(i["fim_s"] for i in itens)
    tipos = list(rel["resumo"]["por_tipo"])
    plot = _LARGURA - _ESQ - _DIR
    base = _TOPO + len(tipos) * _LINHA
    altura = base + _EIXO

    def x(t: float) -> float:
        return _ESQ + plot * min(max(t, 0.0), duracao) / duracao

    partes = [
        f'<svg class="linha-do-tempo" viewBox="0 0 {_LARGURA} {altura}" role="img" '
        'aria-labelledby="tl-titulo tl-desc">',
        '<title id="tl-titulo">Linha do tempo dos desvios</title>',
        f'<desc id="tl-desc">{len(itens)} desvio(s) em {len(tipos)} tipo(s), ao longo de '
        f"{_s(duracao)} de vídeo. Os mesmos dados estão na tabela de desvios.</desc>",
    ]
    for k, tipo in enumerate(tipos):
        y = _TOPO + k * _LINHA
        info = rel["resumo"]["por_tipo"][tipo]
        partes.append(
            f'<line class="grade" x1="{_ESQ}" x2="{_LARGURA - _DIR}" '
            f'y1="{y + _LINHA}" y2="{y + _LINHA}"/>'
        )
        partes.append(
            f'<text class="rotulo" x="{_ESQ - 10}" y="{y + _LINHA / 2 + 4.5:.1f}" '
            f'text-anchor="end">{_e(info["rotulo"])} ({info["total"]})</text>'
        )
        for item in (i for i in itens if i["tipo"] == tipo):
            x0, x1 = x(item["inicio_s"]), x(item["fim_s"])
            largura = max(x1 - x0, 4.0)
            titulo = (
                f"{item['tipo_rotulo']} · severidade {SEVERIDADE_ROTULO[item['severidade']]} · "
                f"{_s(item['inicio_s'])} a {_s(item['fim_s'])} ({_s(item['duracao_s'])}) · "
                f"confiança {_n(item['confianca'], 2)}"
            )
            partes.append(
                f'<rect class="marca m-{_e(item["severidade"])}" x="{x0:.1f}" '
                f'y="{y + 6}" width="{largura:.1f}" height="{_LINHA - 12}" rx="4">'
                f"<title>{_e(titulo)}</title></rect>"
            )
    partes.append(
        f'<line class="base" x1="{_ESQ}" x2="{_LARGURA - _DIR}" y1="{base}" y2="{base}"/>'
    )
    for fracao in (0, 0.25, 0.5, 0.75, 1):
        xt = _ESQ + plot * fracao
        ancora = "start" if fracao == 0 else ("end" if fracao == 1 else "middle")
        partes.append(
            f'<line class="base" x1="{xt:.1f}" x2="{xt:.1f}" y1="{base}" y2="{base + 4}"/>'
            f'<text x="{xt:.1f}" y="{base + 18}" text-anchor="{ancora}">'
            f"{_s(duracao * fracao)}</text>"
        )
    partes.append("</svg>")
    legenda = "".join(
        f'<li><span class="sev"><i class="sw sw-{s}" aria-hidden="true"></i>'
        f"Severidade {SEVERIDADE_ROTULO[s].lower()}</span></li>"
        for s in ("baixa", "media", "alta")
    )
    return "".join(partes) + f'<ul class="legenda">{legenda}</ul>'


# --------------------------------------------------------------------------- #
# Seções
# --------------------------------------------------------------------------- #


def _faixa_de_status(rel: Mapping[str, Any]) -> str:
    resumo = rel["resumo"]
    if resumo["total_desvios"] == 0:
        if rel["analise"]["confiavel"]:
            classe, icone = "status-ok", "✔"
        else:
            classe, icone = "status-aviso", "⚠"
        detalhe = ""
    else:
        classe, icone = "status-desvio", "▲"
        sev = resumo["por_severidade"]
        detalhe = (
            f" Severidade: {sev['alta']} alta, {sev['media']} média, {sev['baixa']} baixa."
        )
    return (
        f'<div class="status {classe}" role="status"><span class="icone" aria-hidden="true">'
        f"{icone}</span><span><strong>{_e(resumo['mensagem'])}</strong>{_e(detalhe)}</span></div>"
    )


def _identificacao(rel: Mapping[str, Any]) -> str:
    v = rel["video"]
    sha = v["sha256"]
    linhas = [
        ("Identificação do vídeo", f"<code>{_e(v['id'])}</code>"),
        ("Tipo de procedimento", _e(v["tipo_procedimento"])),
        ("Perfil aplicado", f"<code>{_e(v['perfil'])}</code>"),
        ("Padrão esperado", _e(v["padrao_esperado"]) or "—"),
        ("Duração", _s(v["duracao_s"])),
        ("Quadros analisados", _e(v["quadros_analisados"])),
        ("Resolução e taxa", f"{_e(v['resolucao'] or '—')} · {_n(v['fps'], 1)} quadros/s"),
        ("SHA-256 do arquivo", f"<code>{_e(sha)}</code>" if sha else "—"),
    ]
    itens = "".join(f"<dt>{_e(k)}</dt><dd>{valor}</dd>" for k, valor in linhas)
    return (
        "<section><h2>Identificação</h2>"
        f'<dl class="grade">{itens}</dl>'
        '<p class="nota">O vídeo é identificado só por este código e pelo SHA-256. O relatório '
        "não traz nome de arquivo, caminho, paciente nem leito.</p></section>"
    )


def _resumo(rel: Mapping[str, Any]) -> str:
    r = rel["resumo"]
    kpis = [
        (str(r["total_desvios"]), "desvios detectados"),
        (_pct(r["pct_tempo_em_desvio"]), "do vídeo em desvio"),
        (str(r["por_severidade"]["alta"]), "de severidade alta"),
        (_s(r["duracao_video_s"]), "de duração"),
    ]
    tiles = "".join(
        f'<div class="kpi"><div class="kpi-valor">{_e(v)}</div>'
        f'<div class="kpi-rotulo">{_e(rotulo)}</div></div>'
        for v, rotulo in kpis
    )
    if r["por_tipo"]:
        linhas = "".join(
            f"<tr><td>{_e(i['rotulo'])}</td><td class='num'>{i['total']}</td>"
            f"<td class='num'>{_s(i['tempo_em_desvio_s'])}</td>"
            f"<td class='num'>{_pct(i['pct_do_tempo'])}</td></tr>"
            for i in r["por_tipo"].values()
        )
        tabela = (
            "<table><caption>Desvios por tipo</caption><thead><tr><th>Tipo</th>"
            '<th class="num">Desvios</th><th class="num">Tempo em desvio</th>'
            '<th class="num">% do vídeo</th></tr></thead>'
            f"<tbody>{linhas}</tbody></table>"
            '<p class="nota">O % do vídeo em desvio une os trechos que se sobrepõem, então '
            "um mesmo instante não conta duas vezes.</p>"
        )
    else:
        tabela = '<p class="nota">Nenhum desvio por tipo: o vídeo ficou dentro do padrão.</p>'
    return f'<section><h2>Resumo</h2><div class="kpis">{tiles}</div>{tabela}</section>'


def _lista_de_desvios(rel: Mapping[str, Any], imagens: Mapping[str, str]) -> str:
    itens = rel["desvios"]
    if not itens:
        return ""
    linhas = []
    for i in itens:
        ev = i["evidencia"]
        if ev["uri"] and ev["uri"] in imagens:
            legenda = f"Quadro {ev['frame_index']} · {_s(ev['tempo_s'])}"
            evidencia = (
                f'<figure class="ev"><img src="{imagens[ev["uri"]]}" '
                f'alt="Quadro de evidência do desvio {i["numero"]}: {_e(i["tipo_rotulo"])}">'
                f"<figcaption>{_e(legenda)}</figcaption></figure>"
            )
        else:
            evidencia = f"Quadro {_e(ev['frame_index'])} indisponível"
        confianca = _n(i["confianca"], 2)
        baixa = (
            '<span class="baixa-conf">baixa confiança: conferir no quadro</span>'
            if i["baixa_confianca"]
            else ""
        )
        linhas.append(
            f"<tr><td class='num'>{i['numero']}</td>"
            f"<td>{_e(i['tipo_rotulo'])}<span class='baixa-conf'>{_e(i['regra'])}</span></td>"
            f"<td>{_sev(i['severidade'])}</td>"
            f"<td>{_s(i['inicio_s'])} a {_s(i['fim_s'])}</td>"
            f"<td class='num'>{_s(i['duracao_s'])}</td>"
            f"<td class='num' title='{_e(i['confianca_origem'])}'>{confianca}{baixa}</td>"
            f"<td>{_e(i['resumo'])}</td><td>{evidencia}</td></tr>"
        )
    return (
        "<section><h2>Desvios detectados</h2>"
        f"{_linha_do_tempo(rel)}"
        "<table><caption>Tempos em segundos desde o início do vídeo. Confiança de 0 a 1: "
        "passe o cursor para ver de onde vem.</caption><thead><tr><th class='num'>Nº</th>"
        "<th>Tipo</th><th>Severidade</th><th>Início e fim</th><th class='num'>Duração</th>"
        "<th class='num'>Confiança</th><th>O que aconteceu</th><th>Evidência</th></tr></thead>"
        f"<tbody>{''.join(linhas)}</tbody></table></section>"
    )


def _repeticoes(rel: Mapping[str, Any]) -> str:
    rep = rel.get("repeticoes")
    if not rep:
        return ""
    esperadas = rep["esperadas"]
    esperado = f" (esperadas de {esperadas[0]} a {esperadas[1]})" if esperadas else ""
    def dentro_do_alvo(r: Mapping[str, Any]) -> str:
        if r["referencia"]:
            return "referência"  # as primeiras repetições só servem de padrão
        return "não" if r["atingiu_alvo"] is False else "sim"

    linhas = "".join(
        f"<tr><td class='num'>{r['numero']}</td><td>{_s(r['inicio_s'])} a {_s(r['fim_s'])}</td>"
        f"<td class='num'>{_n(r['pico'], 0)}°</td><td>{dentro_do_alvo(r)}</td></tr>"
        for r in rep["repeticoes"]
    )
    return (
        f"<section><h2>Repetições</h2><p>{rep['contadas']} repetições contadas{esperado}. "
        f"Ângulo principal: <code>{_e(rep['angulo'])}</code>.</p>"
        "<table><thead><tr><th class='num'>Nº</th><th>Trecho</th><th class='num'>Pico</th>"
        f"<th>Dentro do alvo</th></tr></thead><tbody>{linhas}</tbody></table></section>"
    )


def _avisos(rel: Mapping[str, Any]) -> str:
    a = rel["analise"]
    cobertura = (
        f"Pessoa detectada em {_pct(a['cobertura_pose_pct'])} dos quadros "
        f"(mínimo {_pct(a['cobertura_minima_pct'])} para a análise postural valer). "
        if a["cobertura_pose_pct"] is not None
        else "Análise sem pose: só regras de área crítica foram avaliadas. "
    )
    confiavel = "A análise é confiável." if a["confiavel"] else "A análise é pouco confiável."
    lista = "".join(f"<li>{_e(x)}</li>" for x in rel["avisos"])
    avisos = f'<ul class="avisos">{lista}</ul>' if lista else ""
    return (
        "<section><h2>Qualidade da análise</h2>"
        f"<p>{_e(cobertura + confiavel)}</p>{avisos}</section>"
    )


def _rotulo_chave(chave: str) -> str:
    return _ROTULO_CHAVE.get(chave, chave)


def _dl(valor: Any, chave: str | None = None) -> str:
    """Configuração aninhada como lista de definições; listas de objetos viram tabela."""
    if isinstance(valor, dict):
        itens = "".join(
            f"<dt>{_e(_rotulo_chave(k))}</dt><dd>{_dl(v, k)}</dd>"
            for k, v in valor.items()
            if v is not None and v != {} and v != []
        )
        return f'<dl class="grade aninhado">{itens}</dl>'
    if isinstance(valor, list) and valor and all(isinstance(v, dict) for v in valor):
        colunas = list(dict.fromkeys(k for v in valor for k in v))
        cab = "".join(f"<th>{_e(_rotulo_chave(c))}</th>" for c in colunas)
        corpo = "".join(
            "<tr>" + "".join(f"<td>{_dl(v.get(c), c)}</td>" for c in colunas) + "</tr>"
            for v in valor
        )
        return f"<table><thead><tr>{cab}</tr></thead><tbody>{corpo}</tbody></table>"
    if isinstance(valor, list):
        return ", ".join(_g(v) for v in valor) or "—"
    if chave == "severidade" and valor in SEVERIDADE_ROTULO:
        return _e(SEVERIDADE_ROTULO[valor])
    return _g(valor)


def _modelos_e_limiares(rel: Mapping[str, Any]) -> str:
    m = rel["modelos"]
    linhas = []
    for nome, chave in (("OpenPose (pose)", "openpose"), ("YOLOv8 (objetos e áreas)", "yolov8")):
        info = m[chave]
        if info["utilizado"]:
            dados = {k: v for k, v in info.items() if k != "utilizado"}
            linhas.append(f"<tr><td>{_e(nome)}</td><td>{_dl(dados)}</td></tr>")
        else:
            linhas.append(
                f"<tr><td>{_e(nome)}</td><td>Não utilizado: {_e(info['motivo'])}</td></tr>"
            )
    linhas.append(f"<tr><td>Regras de desvio</td><td>{_e(m['regras_de_desvio'])}</td></tr>")
    linhas.append(f"<tr><td>Relatório</td><td>{_e(m['relatorio'])}</td></tr>")
    lim = rel["limiares"]
    perfil = lim["perfil"]
    secoes = []
    for chave, rotulo in _ROTULO_SECAO.items():
        valor = perfil.get(chave)
        if valor in (None, {}, []):
            continue
        secoes.append(f"<h3>{_e(rotulo)}</h3>{_dl(valor)}")
    us07 = f"<h3>Detecção YOLOv8 (US07)</h3>{_dl(lim['us07'])}" if lim["us07"] else ""
    return (
        "<section><h2>Modelos e limiares usados</h2>"
        "<table><caption>Versões dos modelos</caption><thead><tr><th>Componente</th>"
        f"<th>Versão e parâmetros</th></tr></thead><tbody>{''.join(linhas)}</tbody></table>"
        f"<h3>Limiares de desvio (versão {_e(lim['versao'])}, perfil "
        f"<code>{_e(perfil['id'])}</code>)</h3>"
        f"<p class='nota'>Arquivo: <code>{_e(lim['arquivo'] or '—')}</code>. Janela mínima "
        f"geral: {_s(lim['geral']['janela_minima_s'])}; lacuna máxima: "
        f"{_s(lim['geral']['lacuna_max_s'])}.</p>"
        f"{''.join(secoes)}{us07}</section>"
    )


def _alertas(rel: Mapping[str, Any]) -> str:
    a = rel["alertas"]
    if a["enviados_ao_motor"]:
        lista = "".join(
            f"<li>{_e(i['tipo_rotulo'])} aos {_s(i['inicio_s'])} "
            f"(<code>{_e(i['event_id'])}</code>)</li>"
            for i in a["enviados_ao_motor"]
        )
        graves = (
            f"<p>{a['desvios_severidade_alta']} desvio(s) de severidade alta enviado(s) ao "
            f'motor de alertas (US19):</p><ul class="avisos">{lista}</ul>'
        )
    else:
        graves = f"<p>Desvios de severidade alta: {a['desvios_severidade_alta']}.</p>"
    falhas = "".join(f"<li>{_e(f)}</li>" for f in a["falhas"])
    falhas_html = f'<p>Falhas na publicação:</p><ul class="avisos">{falhas}</ul>' if falhas else ""
    return (
        f"<section><h2>Alertas</h2>{graves}{falhas_html}"
        f"<p class='nota'>{_e(a['observacao'])}</p></section>"
    )


def renderizar(rel: Mapping[str, Any], imagens: Mapping[str, str] | None = None) -> str:
    """A página completa do relatório. ``imagens`` mapeia o URI do quadro ao ``data:`` URI."""
    imagens = imagens or {}
    v = rel["video"]
    titulo = f"Relatório de desvios · {v['id']}"
    corpo = "".join(
        (
            "<header><h1>Relatório de desvios no procedimento</h1>"
            f'<p class="sub">Vídeo <strong>{_e(v["id"])}</strong> · {_e(v["tipo_procedimento"])} '
            f"· gerado em {_e(rel['gerado_em'])}</p></header>",
            _faixa_de_status(rel),
            _identificacao(rel),
            _resumo(rel),
            _lista_de_desvios(rel, imagens),
            _repeticoes(rel),
            _avisos(rel),
            _modelos_e_limiares(rel),
            _alertas(rel),
            f"<footer><p>{_e(rel['aviso_legal'])}</p>"
            f"<p>{_e(rel['model_version'])} · esquema {_e(rel['schema_version'])}.</p></footer>",
        )
    )
    return (
        '<!DOCTYPE html>\n<html lang="pt-BR"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_e(titulo)}</title><style>{_CSS}</style></head><body><main>{corpo}</main>"
        "</body></html>\n"
    )


def embutir_imagens(
    rel: Mapping[str, Any], raiz: Path, cfg: ConfigEvidencia
) -> dict[str, str]:
    """Lê os quadros de evidência, reduz e devolve ``{uri: data URI}``. Quadro ilegível é pulado."""
    imagens: dict[str, str] = {}
    raiz = raiz.resolve()
    for item in rel["desvios"]:
        uri = item["evidencia"]["uri"]
        if not uri or uri in imagens:
            continue
        caminho = (raiz / uri).resolve()
        if not caminho.is_relative_to(raiz) or not caminho.is_file():
            log.warning("quadro de evidência %s não encontrado; fica sem imagem no HTML", uri)
            continue
        try:
            imagem = cv2.imread(str(caminho))
            if imagem is None:
                raise ValueError("arquivo de imagem ilegível")
            altura, largura = imagem.shape[:2]
            if largura > cfg.largura_html_px:
                nova_altura = max(1, round(altura * cfg.largura_html_px / largura))
                imagem = cv2.resize(
                    imagem, (cfg.largura_html_px, nova_altura), interpolation=cv2.INTER_AREA
                )
            ok, buffer = cv2.imencode(
                ".jpg", imagem, [cv2.IMWRITE_JPEG_QUALITY, cfg.qualidade_jpeg]
            )
            if not ok:
                raise ValueError("falha ao codificar o JPG")
        except (ValueError, cv2.error) as erro:
            log.warning("quadro de evidência %s não pôde ser embutido: %s", uri, erro)
            continue
        codificada = base64.b64encode(buffer.tobytes()).decode("ascii")
        imagens[uri] = f"data:image/jpeg;base64,{codificada}"
    return imagens


__all__ = ["COR_SEVERIDADE", "SEVERIDADE_ROTULO", "embutir_imagens", "renderizar"]
