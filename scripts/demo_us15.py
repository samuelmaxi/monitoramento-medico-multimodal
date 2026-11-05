"""Roda e confere cada item da DoD da US15.

uv run python scripts/demo_us15.py                 # todos
uv run python scripts/demo_us15.py news2 gatilhos  # só alguns
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.padding import Padding
from rich.table import Table

from contratos import ChavePseudonimizacaoAusente, pseudonimizar_paciente
from sinais import DetectorSinais, Medicao, mimic, news2

console = Console(highlight=False)
PACIENTE = "pt_" + "0" * 24
T0 = datetime(2026, 1, 1, tzinfo=UTC)
RELATORIO = RAIZ / "saida/sinais/relatorio_us15.json"
ITENS = [
    "dados",
    "limpeza",
    "news2",
    "gatilhos",
    "ml",
    "streaming",
    "avaliacao",
    "azure",
    "api",
]

NOMES = {
    "fc": "FC (bpm)",
    "pas": "PAS (mmHg)",
    "pad": "PAD (mmHg)",
    "spo2": "SpO2 (%)",
    "fr": "FR (irpm)",
    "temp": "Temp (°C)",
    "o2_suplementar": "O2 suplementar",
    "alerta": "Consciência",
}
COR_SEVERIDADE = {"alta": "red", "media": "yellow", "baixa": "cyan", "info": "dim"}
GATILHO = {None: "-", news2.URGENTE: "urgente", news2.EMERGENCIA: "emergência"}

# Valores de borda e pontos esperados, tirados da tabela de referência da task.
REFERENCIA_NEWS2 = {
    "fr": [(8, 3), (9, 1), (11, 1), (12, 0), (20, 0), (21, 2), (24, 2), (25, 3)],
    "spo2": [(91, 3), (92, 2), (93, 2), (94, 1), (95, 1), (96, 0)],
    "o2_suplementar": [(1, 2), (0, 0)],
    "pas": [
        (90, 3),
        (91, 2),
        (100, 2),
        (101, 1),
        (110, 1),
        (111, 0),
        (219, 0),
        (220, 3),
    ],
    "fc": [
        (40, 3),
        (41, 1),
        (50, 1),
        (51, 0),
        (90, 0),
        (91, 1),
        (110, 1),
        (111, 2),
        (130, 2),
        (131, 3),
    ],
    "alerta": [(1, 0), (0, 3)],
    "temp": [
        (35.0, 3),
        (35.1, 1),
        (36.0, 1),
        (36.1, 0),
        (38.0, 0),
        (38.1, 1),
        (39.0, 1),
        (39.1, 2),
    ],
}

resultados: list[tuple[str, bool]] = []


def tabela(*colunas: str) -> Table:
    return Table(
        *colunas, box=box.SIMPLE, header_style="bold", pad_edge=False, show_edge=False
    )


def ok_ou_falha(ok: bool) -> str:
    return "ok" if ok else "[red]FALHOU[/]"


def titulo(numero: int, texto: str) -> None:
    console.print(f"\n[bold]\\[{numero}/9] {texto}[/]")


def mostrar(*partes) -> None:
    for parte in partes:
        console.print(Padding(parte, (0, 0, 0, 4)))


def fim(texto: str, ok: bool, detalhe: str = "") -> None:
    resultados.append((texto, ok))
    status = "[green]ok[/]" if ok else "[bold red]FALHOU[/]"
    console.print(f"    {status}" + (f" [dim]({detalhe})[/]" if detalhe else ""))


def carregar_mimic():
    if not mimic.CHARTEVENTS.exists():
        mostrar(
            f"[red]{mimic.CHARTEVENTS} não encontrado; rode scripts/baixar_sinais.py[/]"
        )
        return None
    with console.status("lendo chartevents..."):
        return mimic.carregar()


def item_dados() -> None:
    titulo(1, "Séries por paciente")
    df = carregar_mimic()
    if df is None:
        return fim("dados", False)
    t = tabela("sinal", "medições", "pacientes", "mín", "mediana", "máx")
    for sinal, nome in NOMES.items():
        g = df[df.sinal == sinal]
        t.add_row(
            nome,
            str(len(g)),
            str(g.subject_id.nunique()),
            f"{g.valor.min():.1f}",
            f"{g.valor.median():.1f}",
            f"{g.valor.max():.1f}",
        )
    mostrar(
        f"MIMIC-IV Demo: {df.subject_id.nunique()} pacientes, {len(df)} medições", t
    )
    fim(
        "dados",
        {"fc", "pas", "pad", "spo2"} <= set(df.sinal),
        "valores brutos, antes da limpeza",
    )


def item_limpeza() -> None:
    titulo(2, "Limpeza de artefatos e dados faltantes")
    d = DetectorSinais()
    t1 = tabela("entrada", "esperado", "resultado", "")
    ok = True
    for sinal, valor, descartar in [
        ("spo2", 0, True),
        ("fc", 350, True),
        ("pas", 999, True),
        ("temp", float("nan"), True),
        ("fc", 75, False),
    ]:
        antes = d.artefatos.total()
        d.processar(Medicao(PACIENTE, T0, sinal, valor))
        descartado = d.artefatos.total() > antes
        ok &= descartado == descartar
        t1.add_row(
            f"{NOMES[sinal]} = {valor:g}",
            "descarta" if descartar else "aceita",
            "descartado" if descartado else "aceito",
            ok_ou_falha(descartado == descartar),
        )

    d = DetectorSinais()
    t2 = tabela("t", "entrada", "evento", "obs", "")
    for horas, sinal, valor, esperado, obs in [
        (0, "fr", 25, [news2.URGENTE], ""),
        (3, "fc", 70, [], "FR anterior expirou (validade 2 h)"),
        (3, "fr", 26, [news2.URGENTE], "NEWS2 parcial"),
    ]:
        eventos = d.processar(
            Medicao(PACIENTE, T0 + timedelta(hours=horas), sinal, valor)
        )
        tipos = [e.event_type for e in eventos]
        ok &= tipos == esperado
        if eventos and obs:
            obs += f", faltando: {eventos[0].evidence.features['news2_ausentes']}"
        t2.add_row(
            f"+{horas}h",
            f"{NOMES[sinal]} = {valor}",
            ", ".join(GATILHO[t] for t in tipos) or "-",
            obs,
            ok_ou_falha(tipos == esperado),
        )
    mostrar("valores impossíveis:", t1, "dados faltantes:", t2)
    fim("limpeza", ok, "regras em docs/arquitetura/us15_sinais_vitais.md")


def item_news2() -> None:
    titulo(3, "NEWS2 por parâmetro")
    t = tabela("parâmetro", "valor:pontos", "")
    ok_geral = True
    for parametro, casos in REFERENCIA_NEWS2.items():
        celulas, ok = [], True
        for valor, esperado in casos:
            obtido = news2.pontos_parametro(parametro, valor)
            ok &= obtido == esperado
            celulas.append(
                f"{valor:g}:{obtido}"
                if obtido == esperado
                else f"[red]{valor:g}:{obtido} (esperado {esperado})[/]"
            )
        ok_geral &= ok
        t.add_row(NOMES[parametro], "  ".join(celulas), ok_ou_falha(ok))
    mostrar(t)
    fim("news2", ok_geral, "bordas de cada faixa da tabela RCP 2017")


def item_gatilhos() -> None:
    titulo(4, "Gatilhos NEWS2")
    normal = {
        "fr": 16,
        "spo2": 98,
        "pas": 120,
        "fc": 75,
        "temp": 37.0,
        "o2_suplementar": 0,
        "alerta": 1,
    }
    t = tabela("cenário", "alterado", "total", "esperado", "obtido", "")
    ok = True
    for nome, alteracoes, esperado in [
        ("estável", {}, None),
        ("total 4", {"fc": 95, "spo2": 94, "fr": 22}, None),
        ("total 5", {"fc": 115, "spo2": 94, "fr": 22}, news2.URGENTE),
        ("total 6", {"fc": 115, "spo2": 93, "fr": 22}, news2.URGENTE),
        ("3 num parâmetro", {"fr": 25}, news2.URGENTE),
        ("total 7", {"fc": 115, "spo2": 93, "fr": 22, "temp": 38.5}, news2.EMERGENCIA),
    ]:
        r = news2.calcular(normal | alteracoes)
        ok &= r.gatilho == esperado
        alterado = ", ".join(f"{k}={v:g}" for k, v in alteracoes.items()) or "-"
        t.add_row(
            nome,
            alterado,
            str(r.total),
            GATILHO[esperado],
            GATILHO[r.gatilho],
            ok_ou_falha(r.gatilho == esperado),
        )
    mostrar(t)
    fim("gatilhos", ok, "urgente: total 5-6 ou 3 num parâmetro; emergência: total >= 7")


def item_ml() -> None:
    titulo(5, "ML: desvio da baseline do paciente")
    t = tabela("cenário", "hora", "valor", "evento", "detalhe", "")
    ok = True
    for nome, sinal, valores, esperado in [
        ("FC estável", "fc", [72, 75, 70, 74, 73, 71, 76, 72, 74, 73] * 2, set()),
        (
            "FC sobe de 73 para 110",
            "fc",
            [72, 75, 70, 74, 73, 71, 76, 72, 74, 73, 108, 110, 109],
            {"desvio_baseline"},
        ),
        (
            "SpO2 cai aos poucos",
            "spo2",
            [97, 98, 97, 98, 97, 98, 97, 98, 97, 96, 95, 94, 93, 92],
            {"tendencia_deterioracao"},
        ),
    ]:
        d, achados = DetectorSinais(), []
        for h, v in enumerate(valores):
            for e in d.processar(Medicao(PACIENTE, T0 + timedelta(hours=h), sinal, v)):
                if e.event_type in ("desvio_baseline", "tendencia_deterioracao"):
                    achados.append((h, v, e))
        tipos = {e.event_type for _, _, e in achados}
        passou = esperado <= tipos if esperado else not tipos
        ok &= passou
        if not achados:
            t.add_row(nome, "", "", "nenhum", "", ok_ou_falha(passou))
        for i, (h, v, e) in enumerate(achados):
            t.add_row(
                nome if i == 0 else "",
                str(h),
                str(v),
                e.event_type,
                e.evidence.summary,
                ok_ou_falha(passou) if i == 0 else "",
            )
    mostrar(t)
    fim("ml", ok, "z-score robusto em 12 h + tendência linear em 6 h")


def item_streaming(pacientes: int, max_eventos: int, velocidade: float) -> None:
    titulo(6, f"Streaming (replay, 1 h de dados a cada {3600 / velocidade:g} s)")
    df = carregar_mimic()
    if df is None:
        return fim("streaming", False)
    try:
        pseudonimos = {
            s: pseudonimizar_paciente(s) for s in df.subject_id.unique()[:pacientes]
        }
    except ChavePseudonimizacaoAusente as exc:
        mostrar(f"[red]{exc}[/]")
        return fim("streaming", False)
    df = df[df.subject_id.isin(pseudonimos)]
    inicio = df.groupby("subject_id").instante.transform("min")
    df = df.assign(instante=datetime.now(UTC) + (df.instante - inicio)).sort_values(
        "instante"
    )

    detector, eventos, medicoes, gasto, anterior = DetectorSinais(), 0, 0, 0.0, None
    for linha in df.itertuples():
        instante = linha.instante.to_pydatetime()
        if anterior is not None:
            time.sleep((instante - anterior).total_seconds() / velocidade)
        anterior = instante
        t = time.perf_counter()
        achados = detector.processar(
            Medicao(pseudonimos[linha.subject_id], instante, linha.sinal, linha.valor)
        )
        gasto += time.perf_counter() - t
        medicoes += 1
        for e in achados:
            eventos += 1
            sev = e.severity.value
            console.print(
                f"    {e.timestamp:%H:%M:%S} [{COR_SEVERIDADE[sev]}]{sev:<5}[/] "
                f"{e.event_type:<23} {e.evidence.summary}"
            )
        if eventos >= max_eventos:
            break
    ms = gasto * 1000 / max(medicoes, 1)
    fim(
        "streaming",
        eventos > 0,
        f"{medicoes} medições, {eventos} eventos, {ms:.3f} ms/medição",
    )


def item_avaliacao() -> None:
    titulo(7, "Avaliação com anomalias injetadas")
    if not RELATORIO.exists():
        mostrar("[red]sem relatório; rode scripts/avaliar_us15.py[/]")
        return fim("avaliacao", False)
    rel = json.loads(RELATORIO.read_text())
    teste, ajuste = rel["teste"], rel["ajuste"]
    m = teste["matriz_confusao"]

    matriz = tabela("", "predito anomalia", "predito normal")
    matriz.add_row("anomalia", str(m["vp"]), str(m["fn"]))
    matriz.add_row("normal", str(m["fp"]), str(m["vn"]))

    ok_recall, ok_f1 = teste["recall"] >= 0.80, teste["f1"] >= 0.70
    metricas = tabela("métrica", "ajuste", "teste", "meta", "")
    metricas.add_row(
        "recall",
        f"{ajuste['recall']:.3f}",
        f"{teste['recall']:.3f}",
        ">= 0.80",
        ok_ou_falha(ok_recall),
    )
    metricas.add_row(
        "precisão", f"{ajuste['precisao']:.3f}", f"{teste['precisao']:.3f}", "", ""
    )
    metricas.add_row(
        "f1", f"{ajuste['f1']:.3f}", f"{teste['f1']:.3f}", ">= 0.70", ok_ou_falha(ok_f1)
    )
    for tipo, r in teste["por_tipo"].items():
        if tipo != "negativo":
            metricas.add_row(
                f"recall {tipo}",
                f"{ajuste['por_tipo'][tipo]['recall']:.3f}",
                f"{r['recall']:.3f}",
                "",
                "",
            )

    mostrar(
        f"matriz de confusão, conjunto de teste ({teste['n']} janelas):",
        matriz,
        metricas,
    )
    fim("avaliacao", ok_recall and ok_f1, "detalhes em docs/relatorio_us15_dod.md")


def item_azure() -> None:
    titulo(8, "Sem Azure AI Anomaly Detector")
    fontes = [
        *RAIZ.glob("sinais/*.py"),
        *RAIZ.glob("app/**/*.py"),
        RAIZ / "pyproject.toml",
    ]
    citacoes = [
        str(p.relative_to(RAIZ))
        for p in fontes
        if "anomalydetector" in p.read_text().lower()
    ]
    try:
        import azure.ai.anomalydetector  # noqa: F401

        instalado = True
    except ImportError:
        instalado = False
    t = tabela("checagem", "resultado", "")
    t.add_row(
        "pacote azure-ai-anomalydetector",
        "instalado" if instalado else "não instalado",
        ok_ou_falha(not instalado),
    )
    t.add_row(
        "referências em sinais/, app/ e pyproject.toml",
        ", ".join(citacoes) or "nenhuma",
        ok_ou_falha(not citacoes),
    )
    mostrar(t)
    fim("azure", not instalado and not citacoes)


def item_api() -> None:
    import warnings

    warnings.filterwarnings(
        "ignore", message="Using `httpx` with `starlette.testclient`"
    )
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import sinais_vitais

    titulo(9, "API")
    sinais_vitais._detector = DetectorSinais()  # começa sem baseline
    client = TestClient(app)
    t = tabela("requisição", "status", "resposta", "")
    ok = True

    r = client.post(
        "/sinais-vitais/news2",
        json={"fc": 115, "spo2": 93, "fr": 22, "temp": 38.5, "pas": 0},
    )
    corpo = r.json() if r.status_code == 200 else {}
    passou = corpo.get("gatilho") == news2.EMERGENCIA and corpo.get("artefatos") == [
        "pas"
    ]
    ok &= passou
    t.add_row(
        "POST /sinais-vitais/news2\nfc=115 spo2=93 fr=22 temp=38.5 pas=0",
        str(r.status_code),
        f"total {corpo.get('total')}, {GATILHO.get(corpo.get('gatilho'), '?')}\n"
        f"artefatos: {', '.join(corpo.get('artefatos', []))}",
        ok_ou_falha(passou),
    )

    def medicoes(inicio_h: int, sinal: str, valores: list[float]) -> list[dict]:
        return [
            {
                "patient_id": "paciente-demo",
                "sinal": sinal,
                "valor": v,
                "timestamp": (T0 + timedelta(hours=inicio_h + i)).isoformat(),
            }
            for i, v in enumerate(valores)
        ]

    r = client.post(
        "/sinais-vitais/medicoes",
        json={"medicoes": medicoes(0, "fc", [72, 75, 70, 74, 73, 71, 76])},
    )
    passou = r.status_code == 200 and r.json()["eventos"] == []
    ok &= passou
    t.add_row(
        "POST /sinais-vitais/medicoes\n7 h de FC normal",
        str(r.status_code),
        "sem eventos",
        ok_ou_falha(passou),
    )

    lote = medicoes(7, "fc", [135, 138]) + medicoes(8, "spo2", [0])
    r = client.post("/sinais-vitais/medicoes", json={"medicoes": lote})
    corpo = (
        r.json()
        if r.status_code == 200
        else {"eventos": [], "artefatos_descartados": 0}
    )
    tipos = [e["event_type"] for e in corpo["eventos"]]
    passou = "desvio_baseline" in tipos and corpo["artefatos_descartados"] == 1
    ok &= passou
    paciente = corpo["eventos"][0]["patient_id"] if corpo["eventos"] else "-"
    t.add_row(
        "POST /sinais-vitais/medicoes\nfc=135, fc=138, spo2=0",
        str(r.status_code),
        "\n".join(tipos) + f"\nartefatos: {corpo['artefatos_descartados']}"
        f"\npatient_id: {paciente}",
        ok_ou_falha(passou),
    )
    mostrar(t)
    fim("api", ok, "baseline mantida entre chamadas; ID pseudonimizado na entrada")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Roda e confere cada item da DoD da US15."
    )
    parser.add_argument(
        "itens",
        nargs="*",
        default=ITENS,
        metavar="ITEM",
        help=f"um ou mais de: {', '.join(ITENS)} (padrão: todos)",
    )
    parser.add_argument(
        "--velocidade",
        type=float,
        default=3600,
        help="replay: 3600 = 1 h de dados por segundo",
    )
    parser.add_argument(
        "--eventos", type=int, default=12, help="replay: para após N eventos"
    )
    parser.add_argument(
        "--pacientes", type=int, default=1, help="replay: pacientes do MIMIC"
    )
    args = parser.parse_args(argv)
    if invalidos := set(args.itens) - set(ITENS):
        parser.error(f"item inválido: {', '.join(sorted(invalidos))}")
    load_dotenv(RAIZ / ".env")

    console.print("[bold]US15 - detecção de anomalias em sinais vitais[/]")
    acoes = {
        "dados": item_dados,
        "limpeza": item_limpeza,
        "news2": item_news2,
        "gatilhos": item_gatilhos,
        "ml": item_ml,
        "streaming": lambda: item_streaming(
            args.pacientes, args.eventos, args.velocidade
        ),
        "avaliacao": item_avaliacao,
        "azure": item_azure,
        "api": item_api,
    }
    for item in ITENS:
        if item in args.itens:
            acoes[item]()

    falhas = [nome for nome, ok in resultados if not ok]
    total = len(resultados)
    if falhas:
        console.print(
            f"\n[bold red]{total - len(falhas)}/{total} ok[/], falharam: {', '.join(falhas)}"
        )
    else:
        console.print(f"\n[bold green]{total}/{total} ok[/]")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
