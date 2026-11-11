"""Executa a demonstração do fluxo completo de monitoramento médico multimodal (US20).

Uso:
    python scripts/rodar_demo.py                     # modo padrão (streaming animado 30x)
    python scripts/rodar_demo.py --rapido            # execução instantânea (velocidade 0)
    python scripts/rodar_demo.py --modo real         # conecta aos serviços Azure reais
    python scripts/rodar_demo.py --notificar         # envia e-mail SMTP se configurado
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pipeline import ConfigPipeline, OrquestradorPipeline

console = Console(highlight=False)


def main(argv: list[str] | None = None) -> int:
    load_dotenv(RAIZ / ".env")

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--modo",
        choices=["real", "simulado"],
        default="simulado",
        help="modo de integração Azure ('real' com chaves do .env, 'simulado' com adaptadores locais)",
    )
    parser.add_argument(
        "--velocidade",
        type=float,
        default=30.0,
        help="fator de aceleração do streaming de sinais vitais (padrão 30; use 0 para instantâneo)",
    )
    parser.add_argument(
        "--rapido",
        action="store_true",
        help="atalho para --velocidade 0 (sem espera entre medições de streaming)",
    )
    parser.add_argument(
        "--notificar",
        action="store_true",
        help="dispara e-mail SMTP real para os destinatários configurados em ALERTA_DESTINATARIOS",
    )
    parser.add_argument(
        "--saida",
        type=Path,
        default=Path("saida/demo_ponta_a_ponta"),
        help="diretório para os artefatos de saída (JSONL, relatório estruturado)",
    )
    parser.add_argument(
        "--paciente",
        default="pt_demo_uti07",
        help="identificador/pseudônimo do paciente monitorado",
    )
    parser.add_argument(
        "--leito",
        default="UTI-07",
        help="leito clínico (ex.: UTI-07)",
    )
    parser.add_argument(
        "--video",
        type=Path,
        default=None,
        help="caminho de arquivo de vídeo para processamento YOLO opcional",
    )

    args = parser.parse_args(argv)

    velocidade = 0.0 if args.rapido else args.velocidade

    config = ConfigPipeline.from_env(
        modo=args.modo,
        fator_velocidade=velocidade,
        notificar_smtp=args.notificar,
        saida_dir=args.saida,
        paciente_id=args.paciente,
        leito_id=args.leito,
        video_path=args.video,
    )

    console.print(
        Panel.fit(
            f"[bold cyan]US20 · Pipeline Multimodal Ponta a Ponta[/bold cyan]\n"
            f"Paciente: [yellow]{config.paciente_id}[/yellow] | Leito: [green]{config.leito_id}[/green]\n"
            f"Modo Azure: [magenta]{config.modo.upper()}[/magenta] | Velocidade Streaming: [blue]{velocidade}x[/blue]\n"
            f"Notificação SMTP: [red]{'ATIVA' if config.notificar_smtp else 'OFF'}[/red]",
            box=box.ROUNDED,
        )
    )

    orquestrador = OrquestradorPipeline(config)
    resumo = orquestrador.executar()

    # Exibição do relatório de etapas e tempos
    from pipeline.logger import LoggerPipeline

    logger = LoggerPipeline(config.saida_dir, console=console)
    logger.registros = []
    # Carrega registros salvos no relatório
    if resumo.caminho_relatorio.is_file():
        import json

        dados = json.loads(resumo.caminho_relatorio.read_text(encoding="utf-8"))
        for item in dados.get("etapas", []):
            from pipeline.logger import RegistroEtapa

            logger.registros.append(RegistroEtapa(**item))

    console.print()
    logger.exibir_tabela_resumo()

    # Painel de Risco e Alertas
    console.print()
    cor_risco = "red" if resumo.nivel_risco_final == "alto" else "yellow"
    console.print(
        Panel.fit(
            f"[bold]Resultado da Fusão Multimodal (US18):[/bold]\n"
            f"Score de Risco Combinado: [{cor_risco} bold]{resumo.score_risco_final:.2f}[/{cor_risco} bold] "
            f"([{cor_risco}]{resumo.nivel_risco_final.upper()}[/{cor_risco}])\n"
            f"Total de Achados no Barramento: [cyan]{resumo.total_eventos_barramento}[/cyan]\n"
            f"Total de Alertas Gerados (US19): [bold red]{resumo.total_alertas}[/bold red]\n"
            f"Duração Total do Pipeline: [bold green]{resumo.duracao_total_ms:,.1f} ms[/bold green]",
            title="Inteligência Clínica",
            box=box.DOUBLE,
        )
    )

    console.print(
        f"[dim]Artefatos gerados em {config.saida_dir}:[/dim]\n"
        f"  • Eventos brutos (auditoria): {resumo.caminho_eventos}\n"
        f"  • Relatório de etapas (DoD 4): {resumo.caminho_relatorio}\n"
        + (f"  • Alertas disparados: {resumo.caminho_alertas}\n" if resumo.caminho_alertas else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

