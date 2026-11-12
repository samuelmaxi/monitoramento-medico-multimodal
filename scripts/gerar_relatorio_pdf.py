"""Gera docs/relatorio_tecnico.pdf a partir de docs/relatorio_tecnico.md (US21).

    uv run --with weasyprint python scripts/gerar_relatorio_pdf.py

O .md é a fonte; o PDF é derivado. Links relativos viram links para o GitHub,
porque um PDF não abre os .md do repositório.
"""

import re
import unicodedata
from collections import Counter
from pathlib import Path

from markdown_it import MarkdownIt
from weasyprint import HTML

RAIZ = Path(__file__).resolve().parent.parent
DOCS = RAIZ / "docs"
FONTE = DOCS / "relatorio_tecnico.md"
SAIDA = DOCS / "relatorio_tecnico.pdf"
REPO = "https://github.com/samuelmaxi/monitoramento-medico-multimodal/blob/main/"


def slug(texto: str) -> str:
    """Mesmo slug do GitHub: minúsculas, sem pontuação, espaços viram hífen, acentos mantidos."""
    texto = re.sub(r"<[^>]+>", "", texto).strip().lower()
    texto = "".join(c for c in texto if c.isalnum() or c in " -_" or unicodedata.category(c).startswith("M"))
    return texto.replace(" ", "-")


def link_absoluto(href: str) -> str:
    if href.startswith(("http", "#", "mailto:")):
        return href
    caminho, _, ancora = href.partition("#")
    destino = (DOCS / caminho).resolve().relative_to(RAIZ)
    return REPO + str(destino) + (f"#{ancora}" if ancora else "")


def corpo_html(md: str) -> tuple[str, str]:
    titulo, _, resto = md.partition("\n")
    html = MarkdownIt("commonmark").enable("table").render(resto)

    vistos: Counter = Counter()

    def com_id(m: re.Match) -> str:
        base = slug(m[2])
        ident = base if not vistos[base] else f"{base}-{vistos[base]}"  # repetido ganha -1, -2, como no GitHub
        vistos[base] += 1
        return f'<{m[1]} id="{ident}">{m[2]}</{m[1]}>'

    html = re.sub(r"<(h[234])>(.*?)</\1>", com_id, html)
    html = re.sub(r'href="([^"]+)"', lambda m: f'href="{link_absoluto(m[1])}"', html)

    # O diagrama é largo (2,7:1): sai do meio do texto e vai para uma página em
    # paisagem no fim da seção 2, para o título da seção não ficar sozinho.
    padrao = r'<p><img src="arquitetura/fluxo_multimodal.png" alt="([^"]*)" /></p>\n'
    alt = re.search(padrao, html)[1]
    html = re.sub(padrao, "", html)
    figura = (
        f'<figure class="paisagem"><img src="arquitetura/fluxo_multimodal.png" alt="{alt}">'
        "<figcaption>Figura 1. Fluxo multimodal (US04): fontes, processamento, detecção,"
        " barramento de eventos, fusão e alerta.</figcaption></figure>"
    )
    html = html.replace('<h2 id="3-modelos-por-tipo-de-dado">', figura + '<h2 id="3-modelos-por-tipo-de-dado">', 1)
    html = html.replace("<p>Fonte editável:", "<p>Diagrama na Figura 1, ao fim desta seção. Fonte editável:", 1)
    # A tabela de resultados tem 6 colunas: larguras fixas evitam a coluna "Meta" espremida.
    html = html.replace("<table>\n<thead>\n<tr>\n<th>US</th>", '<table class="resultados">\n<thead>\n<tr>\n<th>US</th>', 1)
    html = re.sub(
        r'<p><img src="(evidencias/[^"]+)" alt="([^"]*)" /></p>',
        r'<figure><img src="\1" alt="\2"><figcaption>\2. Quadro de evidência anonimizado.</figcaption></figure>',
        html,
    )
    return titulo.lstrip("# ").strip(), html


CSS = """
@page {
  size: A4; margin: 24mm 22mm 22mm 22mm;
  @top-left { content: string(secao); font: 8pt "Noto Sans", sans-serif; color: #6b6b6b; }
  @top-right { content: "Monitoramento médico multimodal"; font: 8pt "Noto Sans", sans-serif; color: #6b6b6b; }
  @bottom-right { content: counter(page) " / " counter(pages); font: 8pt "Noto Sans", sans-serif; color: #6b6b6b; }
}
@page capa { margin: 0; @top-left { content: none } @top-right { content: none } @bottom-right { content: none } }
@page larga { size: A4 landscape; margin: 16mm; }

html { font-family: "Noto Serif", "DejaVu Serif", serif; font-size: 10pt; line-height: 1.5; color: #1c1c1c; }
body { margin: 0; }
p { margin: 0 0 6pt; text-align: justify; hyphens: auto; }
a { color: #1f4e79; text-decoration: none; }
strong { font-weight: 700; }

h2, h3, h4 { font-family: "Noto Sans", sans-serif; color: #12263a; line-height: 1.25; break-after: avoid; }
h2 { string-set: secao content(); font-size: 15pt; margin: 0 0 10pt; padding-bottom: 4pt;
     border-bottom: 0.75pt solid #12263a; margin-top: 26pt; }
h2[id^="apêndice"] { break-before: page; margin-top: 0; }
h3 { font-size: 11.5pt; margin: 16pt 0 6pt; }
h3 + p { break-after: avoid; }
td code { overflow-wrap: anywhere; }
h4 { font-size: 10pt; margin: 12pt 0 4pt; }

ul, ol { margin: 0 0 8pt; padding-left: 16pt; }
li { margin-bottom: 3pt; }
li p { margin: 0; }

code { font-family: "DejaVu Sans Mono", monospace; font-size: 8pt; color: #3b3b3b; }
pre { background: #f5f5f2; border-left: 2pt solid #c9c9c0; padding: 6pt 8pt; margin: 4pt 0 10pt;
      white-space: pre-wrap; word-break: break-all; break-inside: avoid; }
pre code { font-size: 7.3pt; line-height: 1.4; }

blockquote { margin: 6pt 0 10pt; padding: 6pt 10pt; border-left: 2pt solid #1f4e79; background: #f3f6f9; }
blockquote p { margin: 0 0 3pt; }

table { width: 100%; border-collapse: collapse; margin: 4pt 0 12pt; font-family: "Noto Sans", sans-serif;
        font-size: 7.8pt; line-height: 1.35; }
thead { display: table-header-group; }
th { text-align: left; font-weight: 600; color: #12263a; border-bottom: 0.9pt solid #12263a; padding: 4pt 5pt; }
td { border-bottom: 0.4pt solid #d6d6d0; padding: 3.5pt 5pt; vertical-align: top; }
tr { break-inside: avoid; }
td code, th code { font-size: 7pt; }
td[style*="center"], th[style*="center"] { white-space: nowrap; }

figure { margin: 8pt 0 12pt; text-align: center; break-inside: avoid; }
figure img { max-width: 78%; max-height: 95mm; border: 0.4pt solid #d6d6d0; }
figcaption { font-family: "Noto Sans", sans-serif; font-size: 8pt; color: #555; margin-top: 4pt; }
table.resultados { table-layout: fixed; font-size: 7.4pt; }
table.resultados th:nth-child(1) { width: 7%; }
table.resultados th:nth-child(2) { width: 20%; }
table.resultados th:nth-child(3) { width: 11%; }
table.resultados th:nth-child(4) { width: 17%; }
table.resultados th:nth-child(5) { width: 9%; }
table.resultados th:nth-child(6) { width: 29%; }
figure.paisagem { page: larga; break-before: page; break-after: page; margin: 0; }
figure.paisagem img { max-width: 100%; max-height: 165mm; border: none; }

.capa { page: capa; height: 297mm; box-sizing: border-box; padding: 62mm 26mm 24mm;
        display: flex; flex-direction: column; font-family: "Noto Sans", sans-serif; }
.capa .rotulo { font-size: 9pt; letter-spacing: 0.08em; text-transform: uppercase; color: #6b6b6b; }
.capa h1 { font-family: "Noto Serif", serif; font-size: 27pt; line-height: 1.15; color: #12263a;
           margin: 10pt 0 8pt; font-weight: 700; }
.capa .sub { font-size: 11.5pt; color: #333; margin: 0; }
.capa .regua { width: 36mm; border-top: 1.2pt solid #12263a; margin: 18pt 0; }
.capa dl { font-size: 9pt; color: #333; margin: 0; display: grid; grid-template-columns: 30mm auto; row-gap: 3pt; }
.capa dt { color: #6b6b6b; }
.capa dd { margin: 0; }
.capa .pe { margin-top: auto; font-size: 8pt; color: #555; border-top: 0.4pt solid #c9c9c0; padding-top: 6pt; }
"""


def main() -> None:
    titulo, corpo = corpo_html(FONTE.read_text(encoding="utf-8"))
    titulo_curto = titulo.split(":", 1)[-1].strip().capitalize()
    capa = f"""
<section class="capa">
  <div class="rotulo">Tech Challenge · Fase 4 · Relatório técnico (US21)</div>
  <h1>{titulo_curto}</h1>
  <p class="sub">Vídeo, áudio, sinais vitais, prescrições e movimentação de pacientes,
  com detecção de anomalias e alertas para a equipe.</p>
  <div class="regua"></div>
  <dl>
    <dt>Repositório</dt><dd>github.com/samuelmaxi/monitoramento-medico-multimodal</dd>
    <dt>Fonte</dt><dd>docs/relatorio_tecnico.md</dd>
    <dt>Data</dt><dd>novembro de 2026</dd>
  </dl>
  <p class="pe">Trabalho acadêmico. Não é dispositivo médico e não foi validado clinicamente.</p>
</section>"""
    html = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<title>{titulo}</title><style>{CSS}</style></head><body>{capa}
<h2 id="introducao" style="break-before: page">Introdução</h2>{corpo}</body></html>"""
    HTML(string=html, base_url=str(DOCS)).write_pdf(SAIDA)
    print(f"PDF gerado: {SAIDA.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
