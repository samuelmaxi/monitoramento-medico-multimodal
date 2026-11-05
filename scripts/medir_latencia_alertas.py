"""Mede a latência ponta a ponta do motor de alertas (US19): ingestão do sinal até o envio.

    python scripts/medir_latencia_alertas.py                 # 100 achados, webhook HTTP local
    python scripts/medir_latencia_alertas.py -n 300 --env    # usa os canais reais do .env

Cada achado percorre o caminho de produção: ``BarramentoLocal`` (publicado de outra
thread, como um detector) → ``MotorAlertas`` → ``Despachante`` → canal. O relógio é o
real: ``ingested_at`` é o instante da publicação e ``sent_at`` o do envio bem-sucedido.

Sem ``--env`` o canal é um servidor HTTP local, que mede o overhead do próprio motor.
Com ``--env`` entram a rede e o provedor reais (SMTP, Teams ou Slack), que é a medida
que o relatório precisa. Sai com código 2 se p95 > 10 s.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import http.server
import json
import sys
import threading
from pathlib import Path

import yaml
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from alertas import ConfigAlertas, Despachante, MotorAlertas, ServicoAlertas, construir  # noqa: E402
from alertas.armazenamento import MemoriaStore  # noqa: E402
from alertas.latencia import resumo_latencia  # noqa: E402
from alertas.notificadores import NotificadorWebhook  # noqa: E402
from alertas.regras import CAMINHO_REGRAS, regras_de_dict  # noqa: E402
from alertas.simulador import CHAVE_SIMULADA, evento_sintetico  # noqa: E402
from contratos import (  # noqa: E402
    BarramentoLocal,
    Modalidade,
    Severidade,
    agora_utc,
    pseudonimizar_paciente,
)

PRIMEIRO_PACIENTE = 10_000_000
TEMPO_MAXIMO_S = 60.0


class _ServidorLocal:
    """Webhook HTTP local que responde 200 a todo POST."""

    def __init__(self) -> None:
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_):
                pass

        self._servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._servidor.server_address[1]}/"
        threading.Thread(target=self._servidor.serve_forever, daemon=True).start()

    def fechar(self) -> None:
        self._servidor.shutdown()
        self._servidor.server_close()


def _servico_local(url: str) -> ServicoAlertas:
    """Serviço só com o webhook local: a política da prioridade alta usa apenas esse canal."""
    dados = yaml.safe_load(CAMINHO_REGRAS.read_text(encoding="utf-8"))
    for politica in dados["politicas"].values():
        politica["canais"] = ["webhook"] if politica["canais"] else []
    armazenamento = MemoriaStore()
    despachante = Despachante({"webhook": NotificadorWebhook(url)}, armazenamento)
    motor = MotorAlertas(armazenamento, regras_de_dict(dados), despachante.acordar)
    return ServicoAlertas(motor, despachante, segredo_ack="medicao")


async def medir(
    servico: ServicoAlertas, quantidade: int, intervalo_s: float = 0.02
) -> dict:
    """Publica ``quantidade`` achados de pacientes distintos e devolve o resumo de latência."""
    barramento = BarramentoLocal()
    servico.assinar(barramento)
    worker = asyncio.create_task(servico.executar())
    await asyncio.sleep(0)
    try:
        for i in range(quantidade):
            evento = evento_sintetico(
                Modalidade.SINAIS_VITAIS,
                "news2_emergencia",
                severidade=Severidade.ALTA,
                paciente_id=pseudonimizar_paciente(PRIMEIRO_PACIENTE + i, chave=CHAVE_SIMULADA),
                agora=agora_utc(),
                resumo="NEWS2 = 8 (SpO2 89%, FC 128 bpm)",
            )
            # Outra thread, como um detector real publicando no barramento.
            await asyncio.to_thread(barramento.emitir, evento)
            await asyncio.sleep(intervalo_s)
        limite = asyncio.get_running_loop().time() + TEMPO_MAXIMO_S
        while asyncio.get_running_loop().time() < limite:
            alertas = servico.armazenamento.listar(limite=quantidade)
            if len(alertas) == quantidade and all(a.sent_at for a in alertas):
                break
            await asyncio.sleep(0.1)
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
    return resumo_latencia(servico.armazenamento.listar(limite=quantidade))


def main(argv: list[str] | None = None) -> int:
    load_dotenv(RAIZ / ".env")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("-n", "--quantidade", type=int, default=100)
    parser.add_argument("--env", action="store_true", help="usa os canais do .env, não o local")
    parser.add_argument("--saida", default=None, help="grava o resumo em JSON neste caminho")
    args = parser.parse_args(argv)

    servidor = None
    if args.env:
        servico, canal = construir(ConfigAlertas.from_env()), "canais do .env"
    else:
        servidor = _ServidorLocal()
        servico, canal = _servico_local(servidor.url), "webhook HTTP local"
    try:
        resumo = asyncio.run(medir(servico, args.quantidade))
    finally:
        if servidor:
            servidor.fechar()

    resumo["canal"] = canal
    print(f"canal: {canal}\nalertas enviados: {resumo['amostras']} de {args.quantidade}")
    if not resumo["amostras"]:
        print("nenhum alerta foi enviado: confira os canais configurados")
        return 1
    print(f"p50: {resumo['p50_s']:.3f} s")
    print(f"p95: {resumo['p95_s']:.3f} s")
    print(f"máximo: {resumo['max_s']:.3f} s")
    veredito = "atendida" if resumo["atendida"] else "NÃO atendida"
    print(f"meta p95 ≤ {resumo['meta_p95_s']:g} s: {veredito}")
    if args.saida:
        texto = json.dumps(resumo, ensure_ascii=False, indent=2)
        Path(args.saida).write_text(texto, encoding="utf-8")
    return 0 if resumo["atendida"] else 2


if __name__ == "__main__":
    sys.exit(main())
