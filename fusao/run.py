"""Calcula o risco multimodal por paciente a partir de arquivos de eventos (US04).

    # todos os achados já gravados pelos módulos
    python -m fusao.run saida/eventos.jsonl saida/prescricoes/eventos.jsonl

    # janela de 30 min terminando num instante fixo, só um paciente
    python -m fusao.run saida/eventos.jsonl --janela-min 30 \\
        --ate 2026-09-26T17:05:00Z --paciente pt_adc8a04498d64597b7bc858f

    # grava os risco_multimodal (para o motor de alertas ou o relatório)
    python -m fusao.run saida/eventos.jsonl --saida saida/fusao/eventos.jsonl

Sem ``--ate``, cada paciente usa o instante do seu achado mais recente.
Eventos fora do contrato são listados e ignorados; sai com código 1 se houver algum.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from contratos import EmissorJsonl, EventoAchado
from contratos.validar import validar_arquivo

from .alinhamento import alinhar
from .config import CAMINHO_PADRAO, ConfigFusao, ErroDeConfiguracao, carregar
from .eventos import ROTULO, evento_de_risco
from .score import Risco, calcular_risco


def riscos_de(
    eventos: list[EventoAchado],
    config: ConfigFusao,
    *,
    ate: datetime | None = None,
    pacientes: set[str] | None = None,
) -> list[Risco]:
    """Risco de cada paciente, do maior para o menor."""
    alinhados = alinhar(eventos, config.janela, ate=ate)
    riscos = [
        calcular_risco(a, config)
        for p, a in alinhados.items()
        if pacientes is None or p in pacientes
    ]
    return sorted(riscos, key=lambda r: r.score, reverse=True)


def tabela(riscos: list[Risco]) -> str:
    colunas = [ROTULO[m][:6] for m in ROTULO]
    linhas = [
        f"{'paciente':<29} {'score':>5} {'nível':<6} "
        + " ".join(f"{c:>6}" for c in colunas) + f" {'conc.':>6} {'isol.':>6}"
    ]
    for r in riscos:
        contrib = r.contribuicoes
        linhas.append(
            f"{r.patient_id:<29} {r.score:>5.2f} {r.nivel:<6} "
            + " ".join(
                f"{contrib[m.value]:>6.2f}" if m in r.presentes else f"{'—':>6}" for m in ROTULO
            )
            + f" {contrib['concordancia']:>6.2f} {r.score_max_isolado:>6.2f}"
        )
    return "\n".join(linhas)


def _instante(texto: str) -> datetime:
    valor = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    if valor.tzinfo is None:
        raise argparse.ArgumentTypeError("use um instante com fuso, ex. 2026-09-26T17:05:00Z")
    return valor.astimezone(UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("arquivos", nargs="+", type=Path, help=".jsonl ou .json no contrato US04")
    parser.add_argument("--config", type=Path, default=CAMINHO_PADRAO)
    parser.add_argument("--janela-min", type=float, help="sobrescreve janela_min do YAML")
    parser.add_argument("--ate", type=_instante, help="fim da janela (ISO-8601 UTC)")
    parser.add_argument("--paciente", action="append", help="pt_<hex>; pode repetir")
    parser.add_argument("--saida", type=Path, help="grava os risco_multimodal neste .jsonl")
    args = parser.parse_args(argv)

    try:
        config = carregar(args.config)
        if args.janela_min is not None:
            config = config.com_janela(args.janela_min)
    except ErroDeConfiguracao as exc:
        print(f"configuração inválida: {exc}", file=sys.stderr)
        return 2

    eventos: list[EventoAchado] = []
    erros = []
    for arquivo in args.arquivos:
        validos, invalidos = validar_arquivo(arquivo)
        eventos.extend(validos)
        erros.extend(invalidos)
    for erro in erros:
        print(f"INVÁLIDO {erro}", file=sys.stderr)

    riscos = riscos_de(
        eventos, config, ate=args.ate, pacientes=set(args.paciente) if args.paciente else None
    )
    janela = config.limiares()["janela_min"]
    print(f"{len(eventos)} achado(s), {len(riscos)} paciente(s), janela {janela} min")
    if riscos:
        print(tabela(riscos))
    if args.saida and riscos:
        EmissorJsonl(args.saida).emitir_varios(evento_de_risco(r, config) for r in riscos)
        print(f"{len(riscos)} risco_multimodal gravado(s) em {args.saida}")
    return 1 if erros else 0


if __name__ == "__main__":
    raise SystemExit(main())
