"""Publica achados sintéticos no barramento e envia os alertas (US19), sem depender dos detectores.

    python scripts/simular_alertas.py                  # cenário do DoD (NEWS2 = 8)
    python scripts/simular_alertas.py --dedup          # teste de controle de fadiga (supressão na janela)
    python scripts/simular_alertas.py --escalonar      # teste de persistência e escalonamento
    python scripts/simular_alertas.py --todos          # um achado por tipo do catálogo
    python scripts/simular_alertas.py --auditoria      # exibe a trilha de auditoria completa

Lê ALERTA_* e SMTP_* do .env. Se ALERTA_STORE=cosmos, grava no Azure Cosmos DB.
Sem canais configurados, os alertas ficam com status ``failed`` (canal não configurado).
Os achados vão para saida/eventos_simulados.jsonl, separados dos eventos reais.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from alertas import (  # noqa: E402
    ConfigAlertas,
    cenario_news2_emergencia,
    construir,
    evento_sintetico,
    todos_os_tipos,
)
from contratos import BarramentoLocal, Modalidade, Severidade, agora_utc  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    load_dotenv(RAIZ / ".env")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--todos", action="store_true", help="um achado por tipo do catálogo")
    parser.add_argument("--severidade", default="media", choices=["baixa", "media", "alta"],
                        help="severidade dos achados de --todos")
    parser.add_argument("--dedup", action="store_true",
                        help="simula múltiplos achados na mesma janela temporal para testar supressão")
    parser.add_argument("--escalonar", action="store_true",
                        help="simula persistência além do limiar para testar escalonamento")
    parser.add_argument("--auditoria", action="store_true",
                        help="exibe os eventos da trilha de auditoria dos alertas processados")
    args = parser.parse_args(argv)

    config = ConfigAlertas.from_env()
    inicio = agora_utc()
    servico = construir(config)
    barramento = BarramentoLocal(Path(config.saida_dir) / "eventos_simulados.jsonl")
    servico.assinar(barramento)

    if args.dedup:
        print(">> [Fadiga de Alarmes] Simulando 3 eventos de alta severidade no mesmo paciente (janela: 5 min)...")
        base = cenario_news2_emergencia(inicio)
        barramento.emitir(base)
        # 2 minutos depois (dentro da janela)
        barramento.emitir(cenario_news2_emergencia(inicio + timedelta(minutes=2)))
        # 4 minutos depois (ainda dentro da janela)
        barramento.emitir(cenario_news2_emergencia(inicio + timedelta(minutes=4)))
        eventos = [base]
    elif args.escalonar:
        print(">> [Escalonamento] Simulando 6 ocorrências de resposta urgente (média) para forçar escalonamento...")
        eventos = []
        for i in range(6):
            ev = evento_sintetico(
                Modalidade.SINAIS_VITAIS,
                "news2_resposta_urgente",
                severidade=Severidade.MEDIA,
                agora=inicio + timedelta(minutes=i),
                resumo=f"NEWS2 = 5 (ocorrência {i + 1})",
            )
            barramento.emitir(ev)
            eventos.append(ev)
    elif args.todos:
        eventos = list(todos_os_tipos(Severidade(args.severidade)))
        barramento.emitir_varios(eventos)
    else:
        eventos = [cenario_news2_emergencia()]
        barramento.emitir_varios(eventos)

    asyncio.run(servico.processar_pendentes())

    # Só os alertas criados ou atualizados por este lote
    alertas = [a for a in servico.armazenamento.listar(limite=500) if a.last_seen_at >= inicio]
    print(f"achados publicados: {len(eventos) if not args.dedup else 3}; alertas criados ou atualizados: {len(alertas)}")
    for status, quantidade in Counter(a.status.value for a in alertas).items():
        print(f"  {status}: {quantidade}")

    if config.armazenamento == "cosmos":
        print(f"alertas persistidos no Azure Cosmos DB (endpoint: {config.cosmos_endpoint}, banco: {config.cosmos_banco})")
    else:
        print(f"alertas em {Path(config.saida_dir) / 'alertas.jsonl'}")

    if args.auditoria or args.dedup or args.escalonar:
        print("\n--- Trilha de Auditoria Auditável ---")
        for a in alertas:
            evs = servico.armazenamento.eventos(a.id)
            print(f"Alerta {a.id} [{a.priority.value.upper()}] (status: {a.status.value}, ocorrências: {a.occurrences}):")
            for ev in evs:
                print(f"  [{ev.at.strftime('%Y-%m-%d %H:%M:%S')}] {ev.type.value.upper()}: {ev.detail}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
