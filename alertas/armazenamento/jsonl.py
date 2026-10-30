"""Armazenamento em JSON Lines (``saida/alertas.jsonl``), o padrão local.

Cada gravação é uma linha nova (estado completo do alerta). Ao subir, o arquivo é
relido e o último estado de cada alerta vence. Isso dá a recuperação: alertas
``pending`` de uma execução anterior voltam para a fila.
"""

from __future__ import annotations

from pathlib import Path

from ..modelos import Alerta, EventoDeAlerta
from .memoria import MemoriaStore


class ArmazenamentoJsonl(MemoriaStore):
    ARQUIVO_ALERTAS = "alertas.jsonl"
    ARQUIVO_EVENTOS = "alertas_eventos.jsonl"

    def __init__(self, pasta: str | Path) -> None:
        super().__init__()
        pasta = Path(pasta)
        pasta.mkdir(parents=True, exist_ok=True)
        self._caminho_alertas = pasta / self.ARQUIVO_ALERTAS
        self._caminho_eventos = pasta / self.ARQUIVO_EVENTOS
        self._carregar()

    def salvar(self, alerta: Alerta) -> None:
        with self._trava:
            super().salvar(alerta)
            self._anexar(self._caminho_alertas, alerta.model_dump_json())

    def registrar_evento(self, evento: EventoDeAlerta) -> None:
        with self._trava:
            super().registrar_evento(evento)
            self._anexar(self._caminho_eventos, evento.model_dump_json())

    def _anexar(self, caminho: Path, linha: str) -> None:
        with caminho.open("a", encoding="utf-8") as arquivo:
            arquivo.write(linha + "\n")

    def _carregar(self) -> None:
        for linha in _linhas(self._caminho_alertas):
            alerta = Alerta.model_validate_json(linha)
            self._alertas[alerta.id] = alerta
        for linha in _linhas(self._caminho_eventos):
            self._eventos.append(EventoDeAlerta.model_validate_json(linha))


def _linhas(caminho: Path) -> list[str]:
    if not caminho.exists():
        return []
    return [linha for linha in caminho.read_text(encoding="utf-8").splitlines() if linha.strip()]
