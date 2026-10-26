"""Avaliação da US13: recall, precision e F1 dos termos críticos nas amostras anotadas.

    python scripts/eval_us13.py
    python scripts/eval_us13.py --annotations data/text_samples/annotations.csv \
        --results outputs/text_analysis --output outputs/text_analysis/eval.md

Compara, por consulta, o conjunto de termos críticos anotados com o conjunto de
eventos ``termo_critico`` gerados por ``python -m audio.text.run``. Lista falsos
positivos e falsos negativos (com o motivo, quando a entidade foi extraída e
descartada), os descartes corretos (negação, terceiro, hipotético) e um exemplo
de termo crítico detectado e de negação ignorada para o relatório e o vídeo.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from audio.text.normalize import normalize  # noqa: E402
from contratos.validar import validar_arquivo  # noqa: E402


@dataclass(frozen=True)
class Anotacao:
    termos: dict[str, str]  # termo normalizado → nome como anotado
    casos: tuple[str, ...]
    observacoes: tuple[str, ...]


def ler_anotacoes(caminho: Path) -> dict[str, Anotacao]:
    termos: dict[str, dict[str, str]] = defaultdict(dict)
    casos: dict[str, list[str]] = defaultdict(list)
    obs: dict[str, list[str]] = defaultdict(list)
    with caminho.open(encoding="utf-8", newline="") as f:
        for linha in csv.DictReader(f):
            audio_id = linha["audio_id"].strip()
            if not audio_id:
                continue
            termo = (linha.get("termo") or "").strip()
            if termo:
                termos[audio_id][normalize(termo)] = termo
            else:
                termos.setdefault(audio_id, {})
            if linha.get("caso"):
                casos[audio_id].append(linha["caso"].strip())
            if linha.get("observacao"):
                obs[audio_id].append(linha["observacao"].strip())
    return {a: Anotacao(termos[a], tuple(casos[a]), tuple(obs[a])) for a in termos}


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f1


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _celula(texto: Any) -> str:
    return str(texto if texto is not None else "").replace("|", "\\|").replace("\n", " ")


def avaliar(anotacoes: dict[str, Anotacao], resultados: Path) -> dict[str, Any]:
    linhas = []
    fps, fns, corretos_descartados, ausentes, invalidos = [], [], [], [], []
    motivos = Counter()
    exemplo_termo = exemplo_negacao = None
    sentimento = []
    tp_total = fp_total = fn_total = 0

    for audio_id in sorted(anotacoes):
        esperado = anotacoes[audio_id].termos
        arquivo = resultados / f"{audio_id}.json"
        if not arquivo.is_file():
            ausentes.append(audio_id)
            continue
        eventos, erros = validar_arquivo(arquivo)
        invalidos.extend(map(str, erros))
        detalhes_arq = resultados / "detalhes" / f"{audio_id}.json"
        detalhes = json.loads(detalhes_arq.read_text(encoding="utf-8")) if detalhes_arq.is_file() else {}

        previstos = {}
        for ev in eventos:
            if ev.event_type == "termo_critico":
                previstos[normalize(str(ev.evidence.features["termo"]))] = ev
                if exemplo_termo is None and not ev.evidence.features.get("rebaixada"):
                    exemplo_termo = ev
        tp = esperado.keys() & previstos.keys()
        fp = previstos.keys() - esperado.keys()
        fn = esperado.keys() - previstos.keys()
        tp_total += len(tp)
        fp_total += len(fp)
        fn_total += len(fn)

        entidades = detalhes.get("entidades", [])
        for chave in sorted(fp):
            ev = previstos[chave]
            fps.append((audio_id, ev.evidence.features["termo"], ev.evidence.features.get("trecho"), ev.severity.value))
        for chave in sorted(fn):
            vistas = [e for e in entidades if e.get("termo_critico") and normalize(e["termo_critico"]) == chave]
            if vistas:
                causa = "; ".join(f"“{e['texto']}” descartada ({e['motivo']})" for e in vistas if e.get("motivo")) \
                    or "extraída, mas não emitida"
            else:
                causa = "o serviço não extraiu entidade que case com a lista"
            fns.append((audio_id, esperado[chave], causa))

        for e in detalhes.get("descartes", []):
            motivos[e["motivo"]] += 1
            if e.get("termo_critico") and normalize(e["termo_critico"]) not in esperado:
                corretos_descartados.append((audio_id, e["termo_critico"], e["texto"], e["motivo"], e.get("sentenca")))
                if exemplo_negacao is None and str(e["motivo"]).startswith("certeza=negative"):
                    exemplo_negacao = {"audio_id": audio_id, **e}

        resumo = detalhes.get("sentimento", {}).get("resumo")
        if resumo:
            sentimento.append((audio_id, resumo, any(ev.event_type == "sentimento_negativo" for ev in eventos)))

        linhas.append((audio_id, sorted(esperado.values()),
                       sorted(str(previstos[c].evidence.features["termo"]) for c in previstos),
                       len(tp), len(fp), len(fn), ", ".join(anotacoes[audio_id].casos)))

    return {
        "linhas": linhas, "tp": tp_total, "fp": fp_total, "fn": fn_total, "fps": fps, "fns": fns,
        "corretos_descartados": corretos_descartados, "motivos": motivos, "ausentes": ausentes,
        "invalidos": invalidos, "exemplo_termo": exemplo_termo, "exemplo_negacao": exemplo_negacao,
        "sentimento": sentimento,
    }


def relatorio(r: dict[str, Any], anotacoes_path: Path, resultados: Path) -> str:
    p, rec, f1 = _prf(r["tp"], r["fp"], r["fn"])
    out = [
        "# Avaliação da US13: termos críticos e sentimento",
        "",
        f"Anotações: `{anotacoes_path.as_posix()}` · Resultados: `{resultados.as_posix()}` · "
        f"{len(r['linhas'])} consulta(s) avaliada(s).",
        "",
        "Métrica por consulta: o conjunto de termos críticos anotados é comparado com o conjunto de eventos "
        "`termo_critico` emitidos (um evento por termo e consulta). Sem meta fixa (DoD).",
        "",
        "| Métrica | Valor |",
        "| --- | --- |",
        f"| Verdadeiros positivos | {r['tp']} |",
        f"| Falsos positivos | {r['fp']} |",
        f"| Falsos negativos | {r['fn']} |",
        f"| **Precision** | **{_pct(p)}** |",
        f"| **Recall** | **{_pct(rec)}** |",
        f"| **F1** | **{_pct(f1)}** |",
        "",
    ]
    if r["ausentes"]:
        out += [f"> Sem resultado para: {', '.join(r['ausentes'])}. Rode `python -m audio.text.run` antes.", ""]
    if r["invalidos"]:
        out += ["> **Eventos fora do contrato:**", ""] + [f"> - {e}" for e in r["invalidos"]] + [""]

    out += ["## Por consulta", "", "| Consulta | Esperado | Detectado | TP | FP | FN | Casos |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for audio_id, esp, prev, tp, fp, fn, casos in r["linhas"]:
        out.append(f"| {audio_id} | {_celula(', '.join(esp) or '—')} | {_celula(', '.join(prev) or '—')} | {tp} | {fp} | {fn} | {_celula(casos)} |")

    out += ["", "## Falsos positivos", ""]
    if r["fps"]:
        out += ["| Consulta | Termo | Trecho | Severidade |", "| --- | --- | --- | --- |"]
        out += [f"| {a} | {_celula(t)} | {_celula(tr)} | {s} |" for a, t, tr, s in r["fps"]]
    else:
        out.append("Nenhum.")

    out += ["", "## Falsos negativos", ""]
    if r["fns"]:
        out += ["| Consulta | Termo esperado | O que aconteceu |", "| --- | --- | --- |"]
        out += [f"| {a} | {_celula(t)} | {_celula(c)} |" for a, t, c in r["fns"]]
    else:
        out.append("Nenhum.")

    out += ["", "## Termos críticos corretamente ignorados", "",
            "Entidades da lista de termos críticos que o filtro de asserção descartou e que não estavam anotadas como achado.", ""]
    if r["corretos_descartados"]:
        out += ["| Consulta | Termo | Texto | Motivo | Sentença |", "| --- | --- | --- | --- | --- |"]
        out += [f"| {a} | {_celula(t)} | {_celula(tx)} | `{m}` | {_celula(s)} |" for a, t, tx, m, s in r["corretos_descartados"]]
    else:
        out.append("Nenhum.")
    if r["motivos"]:
        out += ["", "Todos os descartes (qualquer entidade): " + ", ".join(f"`{m}` × {n}" for m, n in r["motivos"].most_common()) + "."]

    out += ["", "## Sentimento por consulta (falas do paciente)", "",
            "| Consulta | Sentenças | Negativas | % negativas | Score médio (pos / neu / neg) | Evento |",
            "| --- | --- | --- | --- | --- | --- |"]
    for audio_id, s, gerou in r["sentimento"]:
        m = s["score_medio"]
        out.append(f"| {audio_id} | {s['total_sentencas']} | {s['sentencas_negativas']} | {_pct(s['pct_negativas'])} | "
                   f"{m['positive']:.2f} / {m['neutral']:.2f} / {m['negative']:.2f} | {'sentimento_negativo' if gerou else '—'} |")

    out += ["", "## Exemplos para o relatório e o vídeo", "", "### Termo crítico detectado", ""]
    if r["exemplo_termo"] is not None:
        out += ["```json", json.dumps(r["exemplo_termo"].para_dict(), ensure_ascii=False, indent=2), "```"]
    else:
        out.append("Nenhum termo crítico detectado.")
    out += ["", "### Negação corretamente ignorada", ""]
    if r["exemplo_negacao"] is not None:
        out += ["Entidade extraída pelo serviço e descartada pelo filtro de asserção (não gerou evento):", "",
                "```json", json.dumps(r["exemplo_negacao"], ensure_ascii=False, indent=2), "```"]
    else:
        out.append("Nenhuma negação de termo crítico descartada nas amostras.")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--annotations", type=Path, default=Path("data/text_samples/annotations.csv"))
    parser.add_argument("--results", type=Path, default=Path("outputs/text_analysis"))
    parser.add_argument("--output", type=Path, default=None, help="padrão: <results>/eval.md")
    args = parser.parse_args(argv)

    anotacoes = ler_anotacoes(args.annotations)
    r = avaliar(anotacoes, args.results)
    destino = args.output or args.results / "eval.md"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(relatorio(r, args.annotations, args.results), encoding="utf-8")

    p, rec, f1 = _prf(r["tp"], r["fp"], r["fn"])
    print(f"precision={_pct(p)} recall={_pct(rec)} F1={_pct(f1)} (TP={r['tp']} FP={r['fp']} FN={r['fn']}) → {destino}")
    return 1 if r["ausentes"] or r["invalidos"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
