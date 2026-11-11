"""Logger estruturado por etapa do pipeline (DoD 4)."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

log = logging.getLogger("pipeline.us20")


@dataclass
class RegistroEtapa:
    """Registro estruturado de execução de uma etapa."""

    etapa: str
    inicio_utc: str
    fim_utc: str
    duracao_ms: float
    status: str
    eventos_emitidos: int
    detalhes: dict[str, Any] = field(default_factory=dict)


class LoggerPipeline:
    """Registra cronometragem e eventos de cada etapa, gerando relatório estruturado."""

    def __init__(self, saida_dir: Path, console: Console | None = None) -> None:
        self.saida_dir = saida_dir
        self.saida_dir.mkdir(parents=True, exist_ok=True)
        self.console = console or Console(highlight=False)
        self.registros: list[RegistroEtapa] = []
        self._etapa_ativa: str | None = None
        self._inicio_perf: float = 0.0
        self._inicio_dt: datetime = datetime.now(UTC)

    def iniciar_etapa(self, nome: str) -> None:
        self._etapa_ativa = nome
        self._inicio_dt = datetime.now(UTC)
        self._inicio_perf = time.perf_counter()
        log.info(
            json.dumps({
                "evento": "etapa_iniciada",
                "etapa": nome,
                "timestamp": self._inicio_dt.isoformat(),
            })
        )

    def concluir_etapa(
        self,
        eventos_emitidos: int = 0,
        status: str = "sucesso",
        detalhes: dict[str, Any] | None = None,
    ) -> RegistroEtapa:
        fim_perf = time.perf_counter()
        fim_dt = datetime.now(UTC)
        duracao_ms = round((fim_perf - self._inicio_perf) * 1000, 2)
        nome = self._etapa_ativa or "desconhecida"

        registro = RegistroEtapa(
            etapa=nome,
            inicio_utc=self._inicio_dt.isoformat(),
            fim_utc=fim_dt.isoformat(),
            duracao_ms=duracao_ms,
            status=status,
            eventos_emitidos=eventos_emitidos,
            detalhes=detalhes or {},
        )
        self.registros.append(registro)

        log.info(
            json.dumps({
                "evento": "etapa_concluida",
                **asdict(registro),
            }, default=str)
        )
        self._etapa_ativa = None
        return registro

    def salvar_relatorio(self) -> Path:
        caminho = self.saida_dir / "relatorio_execucao.json"
        total_ms = sum(r.duracao_ms for r in self.registros)
        total_eventos = sum(r.eventos_emitidos for r in self.registros)

        relatorio = {
            "gerado_em": datetime.now(UTC).isoformat(),
            "duracao_total_ms": round(total_ms, 2),
            "total_eventos_emitidos": total_eventos,
            "etapas": [asdict(r) for r in self.registros],
        }

        caminho.write_text(
            json.dumps(relatorio, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return caminho

    def exibir_tabela_resumo(self) -> None:
        tabela = Table(title="US20 · Resumo de Execução Ponta a Ponta", show_lines=True)
        tabela.add_column("Etapa", style="bold cyan")
        tabela.add_column("Status", justify="center")
        tabela.add_column("Duração (ms)", justify="right")
        tabela.add_column("Eventos", justify="right")
        tabela.add_column("Destaques", overflow="fold")

        for r in self.registros:
            cor_status = "green" if r.status == "sucesso" else "yellow"
            destaque = ", ".join(f"{k}: {v}" for k, v in list(r.detalhes.items())[:3])
            tabela.add_row(
                r.etapa,
                f"[{cor_status}]{r.status}[/{cor_status}]",
                f"{r.duracao_ms:,.1f}",
                str(r.eventos_emitidos),
                destaque or "—",
            )

        total_ms = sum(r.duracao_ms for r in self.registros)
        total_evs = sum(r.eventos_emitidos for r in self.registros)
        tabela.add_section()
        tabela.add_row(
            "[bold]TOTAL[/bold]",
            "[bold green]concluído[/bold green]",
            f"[bold]{total_ms:,.1f}[/bold]",
            f"[bold]{total_evs}[/bold]",
            "Fluxo completo executado com sucesso",
        )
        self.console.print(tabela)

