"""Detecta anomalias na evolução de prescrições e grava eventos (US04) e relatório.

    # baixa o MIMIC-IV Demo (acesso aberto) e roda nos 100 pacientes
    python -m prescricoes.run --baixar

    # só alguns pacientes (subject_id do MIMIC)
    python -m prescricoes.run --paciente 10002428 --paciente 10019003

Saídas em ``--saida`` (padrão ``saida/prescricoes``): ``eventos.jsonl`` (um
``EventoAchado`` por anomalia e um ``sem_achados`` por internação sem nenhuma)
e ``relatorio.json``. ``PSEUDONYM_KEY`` precisa estar no ambiente (``.env``).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contratos import EmissorJsonl, EventoAchado

from .config import CAMINHO_PADRAO, ConfigPrescricoes, carregar_config
from .eventos import montar_evento, montar_sem_achados, relatorio
from .mimic import DIRETORIO, baixar, carregar_internacoes, carregar_prescricoes
from .modelos import Anomalia, Internacao, Prescricao
from .regras import detectar

log = logging.getLogger("prescricoes.run")


@dataclass(frozen=True)
class Execucao:
    anomalias: list[Anomalia]
    eventos: list[EventoAchado]
    relatorio: dict[str, Any]
    caminho_eventos: Path
    caminho_relatorio: Path


def eventos_de(
    prescricoes: list[Prescricao],
    internacoes: dict[str, Internacao],
    cfg: ConfigPrescricoes,
    *,
    chave: bytes | str | None = None,
) -> tuple[list[Anomalia], list[EventoAchado]]:
    """Anomalias e eventos (inclui ``sem_achados`` das internações limpas)."""
    anomalias = detectar(prescricoes, cfg, internacoes)
    sinteticas = {p.ordem_id for p in prescricoes if p.sintetica}
    eventos = [
        montar_evento(a, cfg, sintetica=bool(sinteticas.intersection(a.ordens)), chave=chave)
        for a in anomalias
    ]
    com_anomalia = {a.internacao for a in anomalias}
    contagem: dict[str, int] = {}
    for p in prescricoes:
        if p.internacao:
            contagem[p.internacao] = contagem.get(p.internacao, 0) + 1
    for hadm, n in sorted(contagem.items()):
        if hadm not in com_anomalia and hadm in internacoes:
            eventos.append(montar_sem_achados(internacoes[hadm], n, cfg, chave=chave))
    return anomalias, eventos


def executar(
    *,
    dados: Path = DIRETORIO,
    config: Path = CAMINHO_PADRAO,
    saida: Path = Path("saida/prescricoes"),
    pacientes: list[str] | None = None,
) -> Execucao:
    cfg = carregar_config(config)
    prescricoes = carregar_prescricoes(dados, pacientes=pacientes)
    internacoes = carregar_internacoes(dados)
    anomalias, eventos = eventos_de(prescricoes, internacoes, cfg)

    saida.mkdir(parents=True, exist_ok=True)
    caminho_eventos = saida / "eventos.jsonl"
    caminho_eventos.unlink(missing_ok=True)
    EmissorJsonl(caminho_eventos).emitir_varios(eventos)
    rel = relatorio(
        anomalias,
        eventos,
        cfg,
        fonte={"dataset": "MIMIC-IV Clinical Database Demo 2.2", "diretorio": str(dados)},
        prescricoes=len(prescricoes),
        internacoes={p.internacao for p in prescricoes if p.internacao},
    )
    caminho_relatorio = saida / "relatorio.json"
    caminho_relatorio.write_text(json.dumps(rel, ensure_ascii=False, indent=2), encoding="utf-8")
    return Execucao(anomalias, eventos, rel, caminho_eventos, caminho_relatorio)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dados", type=Path, default=DIRETORIO, help="pasta com as tabelas do MIMIC")
    parser.add_argument("--config", type=Path, default=CAMINHO_PADRAO)
    parser.add_argument("--saida", type=Path, default=Path("saida/prescricoes"))
    parser.add_argument("--paciente", action="append", help="subject_id (repita para vários)")
    parser.add_argument("--baixar", action="store_true", help="baixa as tabelas que faltarem")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # pragma: no cover
        pass
    if args.baixar:
        baixar(args.dados)
    try:
        resultado = executar(
            dados=args.dados, config=args.config, saida=args.saida, pacientes=args.paciente
        )
    except (FileNotFoundError, ValueError) as erro:
        log.error("erro: %s", erro)
        return 1
    rel = resultado.relatorio
    log.info(
        "%d prescrições, %d internações: %d anomalias (%s), %d alertáveis",
        rel["prescricoes_analisadas"],
        rel["internacoes"],
        rel["total_anomalias"],
        ", ".join(f"{t}={n}" for t, n in rel["por_tipo"].items()),
        rel["alertaveis"],
    )
    log.info("eventos: %s\nrelatório: %s", resultado.caminho_eventos, resultado.caminho_relatorio)
    return 0


if __name__ == "__main__":
    sys.exit(main())
