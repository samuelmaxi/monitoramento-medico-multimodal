"""Emissores de eventos e barramento local: a única porta de saída dos módulos.

Todos aceitam apenas :class:`EventoAchado`. Um dict passado por engano é
validado antes, e o que não segue o contrato é recusado.

- ``BarramentoLocal`` é o ponto de integração do pipeline. Valida, grava no
  JSONL (auditoria, métricas e replay da demo) e entrega o evento, em ordem, a
  quem assinou (fusão, motor de alertas). Substitui um barramento de nuvem sem
  custo nenhum.
- ``EmissorJsonl`` só grava em arquivo; útil para rodar um módulo isolado.
- ``EmissorMemoria`` guarda em lista; usado nos testes.

Os módulos recebem um ``Emissor`` por parâmetro e não sabem qual é, então o
mesmo código roda no teste, isolado ou no pipeline completo.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Union

from .catalogo import Modalidade, Severidade
from .evento import EventoAchado

EventoOuDict = Union[EventoAchado, Mapping[str, Any]]
Filtro = Callable[[EventoAchado], bool]
Assinante = Callable[[EventoAchado], None]

CAMINHO_PADRAO_JSONL = "saida/eventos.jsonl"

log = logging.getLogger(__name__)


def _garantir_evento(evento: EventoOuDict) -> EventoAchado:
    if isinstance(evento, EventoAchado):
        return evento
    return EventoAchado.model_validate(evento)


class Emissor(Protocol):
    def emitir(self, evento: EventoOuDict) -> None: ...

    def emitir_varios(self, eventos: Iterable[EventoOuDict]) -> None: ...


class EmissorMemoria:
    def __init__(self) -> None:
        self.eventos: list[EventoAchado] = []

    def emitir(self, evento: EventoOuDict) -> None:
        self.eventos.append(_garantir_evento(evento))

    def emitir_varios(self, eventos: Iterable[EventoOuDict]) -> None:
        for evento in eventos:
            self.emitir(evento)


class EmissorJsonl:
    """Um evento por linha (JSON Lines). Seguro para várias threads."""

    def __init__(self, caminho: str | Path) -> None:
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self._trava = threading.Lock()

    def emitir(self, evento: EventoOuDict) -> None:
        self.emitir_varios([evento])

    def emitir_varios(self, eventos: Iterable[EventoOuDict]) -> None:
        linhas = [_garantir_evento(e).para_json() + "\n" for e in eventos]
        with self._trava, self.caminho.open("a", encoding="utf-8") as arquivo:
            arquivo.writelines(linhas)


# --------------------------------------------------------------------------- #
# Barramento local
# --------------------------------------------------------------------------- #

def filtro(
    *,
    modalidades: Iterable[Modalidade | str] | None = None,
    exceto_modalidades: Iterable[Modalidade | str] | None = None,
    severidade_minima: Severidade | str | None = None,
) -> Filtro:
    """Monta um filtro de assinatura.

    Exemplos: a fusão assina ``filtro(exceto_modalidades=["fusao"])`` e o
    motor de alertas assina ``filtro(severidade_minima="media")``.
    """
    incluir = {Modalidade(m) for m in modalidades} if modalidades is not None else None
    excluir = {Modalidade(m) for m in exceto_modalidades or ()}
    minima = Severidade(severidade_minima) if severidade_minima is not None else None

    def aceita(evento: EventoAchado) -> bool:
        if incluir is not None and evento.modality not in incluir:
            return False
        if evento.modality in excluir:
            return False
        if minima is not None and evento.severity.peso < minima.peso:
            return False
        return True

    return aceita


@dataclass(frozen=True)
class FalhaAssinante:
    assinante: str
    event_id: str
    erro: str


@dataclass(frozen=True)
class _Assinatura:
    nome: str
    callback: Assinante
    filtro: Filtro


class BarramentoLocal:
    """Barramento em processo, síncrono e ordenado.

    - Todo evento publicado é validado e gravado no JSONL antes da entrega.
    - Assinantes recebem os eventos na ordem de publicação.
    - Um assinante pode publicar durante a entrega (a fusão devolve
      ``risco_multimodal``). O novo evento entra na fila e é entregue depois,
      sem recursão.
    - Se um assinante falhar, a falha é registrada em ``falhas`` e os demais
      assinantes continuam recebendo. Um módulo com erro não derruba os alertas.
    """

    def __init__(self, caminho_jsonl: str | Path | None = None) -> None:
        self._arquivo = EmissorJsonl(caminho_jsonl) if caminho_jsonl is not None else None
        self._assinaturas: list[_Assinatura] = []
        self._fila: deque[EventoAchado] = deque()
        self._entregando = False
        self._trava = threading.RLock()
        self.falhas: list[FalhaAssinante] = []
        self.total_publicados = 0

    @classmethod
    def do_ambiente(cls) -> "BarramentoLocal":
        """Grava em ``EVENTOS_JSONL`` (padrão ``saida/eventos.jsonl``)."""
        return cls(os.environ.get("EVENTOS_JSONL", CAMINHO_PADRAO_JSONL))

    @property
    def caminho(self) -> Path | None:
        return self._arquivo.caminho if self._arquivo else None

    def assinar(self, nome: str, callback: Assinante, filtro: Filtro | None = None) -> None:
        with self._trava:
            self._assinaturas.append(_Assinatura(nome, callback, filtro or (lambda _e: True)))

    def emitir(self, evento: EventoOuDict) -> None:
        self.emitir_varios([evento])

    def emitir_varios(self, eventos: Iterable[EventoOuDict]) -> None:
        validados = [_garantir_evento(e) for e in eventos]
        with self._trava:
            if self._arquivo is not None:
                self._arquivo.emitir_varios(validados)
            self.total_publicados += len(validados)
            self._fila.extend(validados)
            if self._entregando:
                return  # publicação feita por um assinante: o laço externo entrega
            self._entregando = True
            try:
                while self._fila:
                    self._entregar(self._fila.popleft())
            finally:
                self._entregando = False

    def _entregar(self, evento: EventoAchado) -> None:
        for assinatura in list(self._assinaturas):
            if not assinatura.filtro(evento):
                continue
            try:
                assinatura.callback(evento)
            except Exception as exc:  # noqa: BLE001 — isolamento entre assinantes
                log.exception("assinante %s falhou no evento %s", assinatura.nome, evento.event_id)
                self.falhas.append(FalhaAssinante(assinatura.nome, str(evento.event_id), repr(exc)))
