"""Demonstração da US18: fusão multimodal em score de risco, até o alerta da equipe.

    uv run python scripts/demo_us18.py

1. Publica, em ordem cronológica, os achados de dois pacientes no ``BarramentoLocal``:
   - UTI-07: SpO2 em queda + fala com dispneia + relato de falta de ar (sem vídeo);
   - UTI-03: só sinais vitais e prescrições (NEWS2 de emergência).
2. A fusão (US18) assina o barramento e publica ``risco_multimodal`` a cada mudança.
3. O motor de alertas (US19) assina o mesmo barramento e transforma em alerta.

Mostra o risco de cada modalidade isolada × combinada, a contribuição de cada
uma e os alertas gerados. Grava tudo em ``saida/fusao/demo_eventos.jsonl``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.chdir(RAIZ)

from dotenv import load_dotenv  # noqa: E402
from rich import box  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from alertas import ConfigAlertas, construir  # noqa: E402
from contratos import BarramentoLocal, Modalidade, pseudonimizar_paciente  # noqa: E402
from fusao import FusaoMultimodal, alinhar_paciente, calcular_risco, carregar_config  # noqa: E402
from fusao.cenarios import cenario_dispneia, cenario_sem_video  # noqa: E402
from fusao.eventos import ROTULO, ROTULO_EIXO  # noqa: E402

console = Console(highlight=False)
SAIDA = RAIZ / "saida/fusao/demo_eventos.jsonl"
T0 = datetime(2026, 9, 26, 16, 10, tzinfo=UTC)
CHAVE_DEMO = "chave-padrao-demonstracao-us18-32bytes-000000"
COR = {"alto": "red", "medio": "yellow", "baixo": "cyan"}


def _chave() -> str:
    chave = os.getenv("PSEUDONYM_KEY")
    return chave if chave and len(chave.encode()) >= 32 else CHAVE_DEMO


def _fmt(valor: float) -> str:
    return f"{valor:.2f}".replace(".", ",")


def tabela_isolado_vs_combinado(eventos, paciente, config) -> Table:
    ate = max(e.window.end if e.window else e.timestamp for e in eventos)
    completo = calcular_risco(alinhar_paciente(eventos, paciente, ate, config.janela), config)

    tabela = Table(box=box.SIMPLE_HEAVY, title="Risco isolado × combinado")
    for coluna in ("modalidade", "achado mais forte", "risco isolado", "contribuição no combinado"):
        tabela.add_column(coluna)
    for m, sinal in completo.sinais.items():
        if m not in completo.presentes:
            tabela.add_row(ROTULO[m], "[dim]ausente[/]", "—", "—")
            continue
        so_ela = [e for e in eventos if e.modality is m]
        isolado = calcular_risco(alinhar_paciente(so_ela, paciente, ate, config.janela), config)
        tipo = sinal.evento.event_type if sinal.evento else "sem achados"
        nivel = isolado.nivel
        tabela.add_row(
            ROTULO[m], tipo,
            f"[{COR[nivel]}]{_fmt(isolado.score)} ({nivel})[/]",
            _fmt(sinal.contribuicao),
        )
    if completo.concordancia:
        mods = " + ".join(ROTULO[m] for m in completo.concordancia.modalidades)
        eixo = completo.concordancia.eixo
        tabela.add_row(
            "concordância", f"eixo {ROTULO_EIXO.get(eixo, eixo)}: {mods}", "—",
            _fmt(completo.concordancia.contribuicao),
        )
    tabela.add_section()
    tabela.add_row(
        "[b]combinado[/]", "",
        f"[b {COR[completo.nivel]}]{_fmt(completo.score)} ({completo.nivel})[/]",
        f"[b]{_fmt(sum(completo.contribuicoes.values()))}[/]",
    )
    return tabela


def main() -> int:
    load_dotenv()
    logging.basicConfig(level=logging.ERROR)
    chave = _chave()
    config = carregar_config()
    pacientes = {
        "UTI-07": (pseudonimizar_paciente("demo-us18-uti07", chave=chave), cenario_dispneia),
        "UTI-03": (pseudonimizar_paciente("demo-us18-uti03", chave=chave), cenario_sem_video),
    }
    eventos = {leito: fabrica(p, T0) for leito, (p, fabrica) in pacientes.items()}

    SAIDA.unlink(missing_ok=True)
    barramento = BarramentoLocal(SAIDA)
    fusao = FusaoMultimodal(config)
    fusao.assinar(barramento)
    alertas = construir(ConfigAlertas(armazenamento="memoria"))
    alertas.assinar(barramento)

    console.rule("[b]US18 · fusão multimodal em tempo real")
    linha = Table(box=box.SIMPLE, title="Achados chegando no barramento → risco do paciente")
    for coluna in ("hora", "leito", "achado", "severidade", "risco", "publicou risco_multimodal"):
        linha.add_column(coluna)
    em_ordem = sorted(
        ((leito, e) for leito, lista in eventos.items() for e in lista),
        key=lambda par: par[1].timestamp,
    )
    for leito, evento in em_ordem:
        antes = barramento.total_publicados
        barramento.emitir(evento)
        risco = fusao.risco(evento.patient_id)
        publicou = barramento.total_publicados - antes - 1
        linha.add_row(
            evento.timestamp.strftime("%H:%M"), leito,
            f"{evento.modality.value}/{evento.event_type}", evento.severity.value,
            f"[{COR[risco.nivel]}]{_fmt(risco.score)} {risco.nivel}[/]",
            f"sim ({risco.severidade.value})" if publicou else "não (variação pequena)",
        )
    console.print(linha)

    for leito, (paciente, _) in pacientes.items():
        console.rule(f"[b]{leito}")
        console.print(tabela_isolado_vs_combinado(eventos[leito], paciente, config))
        risco = fusao.risco(paciente)
        ausentes = ", ".join(m.value for m in risco.ausentes) or "nenhuma"
        console.print(f"Modalidades ausentes: {ausentes} (contribuição 0, sem erro)")

    asyncio.run(alertas.processar_pendentes())
    console.rule("[b]US19 · alertas para a equipe médica")
    tabela = Table(box=box.SIMPLE)
    for coluna in ("prioridade", "leito", "origem", "resumo"):
        tabela.add_column(coluna, overflow="fold")
    ordem = {"alta": 0, "media": 1, "baixa": 2}
    for alerta in sorted(alertas.armazenamento.listar(), key=lambda a: ordem[a.priority.value]):
        cor = {"alta": "red", "media": "yellow"}.get(alerta.priority.value, "cyan")
        origem = f"{alerta.modalities[0]}/{alerta.event_type}"
        tabela.add_row(f"[{cor}]{alerta.priority.value}[/]", alerta.bed_id or "—", origem,
                       alerta.summary)
    console.print(tabela)

    fusao_publicados = sum(
        1 for linha_json in SAIDA.read_text(encoding="utf-8").splitlines()
        if f'"modality":"{Modalidade.FUSAO.value}"' in linha_json
    )
    console.print(
        f"{barramento.total_publicados} evento(s) no barramento, {fusao_publicados} da fusão; "
        f"falhas de assinante: {len(barramento.falhas)}. Gravado em {SAIDA.relative_to(RAIZ)}"
    )
    return 0 if not barramento.falhas else 1


if __name__ == "__main__":
    raise SystemExit(main())
