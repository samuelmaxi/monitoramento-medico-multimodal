"""Publica achados sintéticos no barramento e envia os alertas (US19), sem depender dos detectores.

    python scripts/simular_alertas.py                  # cenário do DoD (NEWS2 = 8)
    python scripts/simular_alertas.py --todos          # um achado por tipo do catálogo

Lê ALERTA_* e SMTP_* do .env. Sem canais configurados, os alertas ficam em
saida/alertas.jsonl com status ``failed`` (canal não configurado).
Os achados vão para saida/eventos_simulados.jsonl, separados dos eventos reais.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from alertas import ConfigAlertas, cenario_news2_emergencia, construir, todos_os_tipos  # noqa: E402
from contratos import BarramentoLocal, Severidade, agora_utc  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    load_dotenv(RAIZ / ".env")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--todos", action="store_true", help="um achado por tipo do catálogo")
    parser.add_argument("--severidade", default="media", choices=["baixa", "media", "alta"],
                        help="severidade dos achados de --todos")
    args = parser.parse_args(argv)

    config = ConfigAlertas.from_env()
    inicio = agora_utc()
    servico = construir(config)
    barramento = BarramentoLocal(Path(config.saida_dir) / "eventos_simulados.jsonl")
    servico.assinar(barramento)

    if args.todos:
        eventos = list(todos_os_tipos(Severidade(args.severidade)))
    else:
        eventos = [cenario_news2_emergencia()]
    barramento.emitir_varios(eventos)
    asyncio.run(servico.processar_pendentes())

    # Só os alertas criados ou atualizados por este lote; os de execuções anteriores ficam de fora
    alertas = [a for a in servico.armazenamento.listar(limite=500) if a.last_seen_at >= inicio]
    print(f"achados publicados: {len(eventos)}; alertas criados ou atualizados: {len(alertas)}")
    for status, quantidade in Counter(a.status.value for a in alertas).items():
        print(f"  {status}: {quantidade}")
    print(f"alertas em {Path(config.saida_dir) / 'alertas.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
