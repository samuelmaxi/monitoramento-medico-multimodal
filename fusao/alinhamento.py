"""Alinhamento dos achados por ``patient_id`` e janela temporal (funções puras).

Um achado entra na janela ``[ate - janela, ate]`` quando o seu intervalo cruza a
janela. O intervalo é ``window`` quando existe (imobilidade de horas, trecho da
consulta, segmento de vídeo) e o próprio ``timestamp`` quando não existe. Assim
uma imobilidade que começou antes da janela e continua nela conta, e um achado
posterior a ``ate`` não conta (não se usa o futuro para explicar o presente).

O resultado separa três situações por modalidade, que a fusão trata de forma
diferente:

- **ausente**: nenhum evento da modalidade na janela (ex.: paciente sem vídeo);
- **presente e normal**: só ``sem_achados``/``info`` (contribuição zero);
- **com achado**: ao menos um evento de severidade ``baixa`` ou acima.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from contratos import EventoAchado, Modalidade, Severidade

from .config import MODALIDADES_FUNDIDAS


def inicio_do_achado(evento: EventoAchado) -> datetime:
    return evento.window.start if evento.window else evento.timestamp


def fim_do_achado(evento: EventoAchado) -> datetime:
    return evento.window.end if evento.window else evento.timestamp


def na_janela(evento: EventoAchado, ate: datetime, janela: timedelta) -> bool:
    """O intervalo do achado cruza ``[ate - janela, ate]``.

    Usa o início do achado contra ``ate`` (o achado já tinha começado) e o fim
    contra o início da janela (ainda não tinha terminado antes dela).
    """
    return inicio_do_achado(evento) <= ate and fim_do_achado(evento) >= ate - janela


def idade(evento: EventoAchado, ate: datetime) -> timedelta:
    """Tempo desde o fim do achado até ``ate``. Achado em curso tem idade zero."""
    return max(timedelta(0), ate - fim_do_achado(evento))


@dataclass(frozen=True)
class Alinhamento:
    """Achados de um paciente que caem em uma janela, agrupados por modalidade."""

    patient_id: str
    inicio: datetime
    ate: datetime
    por_modalidade: dict[Modalidade, tuple[EventoAchado, ...]]

    @property
    def presentes(self) -> tuple[Modalidade, ...]:
        return tuple(m for m in MODALIDADES_FUNDIDAS if self.por_modalidade.get(m))

    @property
    def ausentes(self) -> tuple[Modalidade, ...]:
        return tuple(m for m in MODALIDADES_FUNDIDAS if not self.por_modalidade.get(m))

    @property
    def com_achado(self) -> tuple[Modalidade, ...]:
        return tuple(
            m for m in self.presentes
            if any(e.severity is not Severidade.INFO for e in self.por_modalidade[m])
        )

    @property
    def eventos(self) -> tuple[EventoAchado, ...]:
        return tuple(e for m in MODALIDADES_FUNDIDAS for e in self.por_modalidade.get(m, ()))

    @property
    def vazio(self) -> bool:
        return not self.presentes


def alinhar_paciente(
    eventos: Iterable[EventoAchado],
    patient_id: str,
    ate: datetime,
    janela: timedelta,
) -> Alinhamento:
    """Achados de ``patient_id`` na janela que termina em ``ate``.

    Eventos de outros pacientes e da própria fusão são ignorados; ``event_id``
    repetido (reentrega do barramento) conta uma vez. Dentro de cada modalidade,
    a ordem é cronológica.
    """
    vistos: set = set()
    grupos: dict[Modalidade, list[EventoAchado]] = defaultdict(list)
    for evento in eventos:
        if (
            evento.patient_id != patient_id
            or evento.modality is Modalidade.FUSAO
            or evento.event_id in vistos
            or not na_janela(evento, ate, janela)
        ):
            continue
        vistos.add(evento.event_id)
        grupos[evento.modality].append(evento)
    return Alinhamento(
        patient_id=patient_id,
        inicio=ate - janela,
        ate=ate,
        por_modalidade={m: tuple(sorted(g, key=fim_do_achado)) for m, g in grupos.items()},
    )


def instante_mais_recente(eventos: Iterable[EventoAchado]) -> datetime | None:
    """Fim do achado mais recente: o "agora" do paciente no replay ou no streaming."""
    return max((fim_do_achado(e) for e in eventos), default=None)


def alinhar(
    eventos: Iterable[EventoAchado],
    janela: timedelta,
    *,
    ate: datetime | None = None,
) -> dict[str, Alinhamento]:
    """Alinha todos os pacientes presentes em ``eventos``.

    Sem ``ate``, cada paciente usa o instante do seu achado mais recente: é o
    que o serviço faz em tempo real, e o que se espera ao reprocessar um JSONL.
    """
    por_paciente: dict[str, list[EventoAchado]] = defaultdict(list)
    for evento in eventos:
        if evento.modality is not Modalidade.FUSAO:
            por_paciente[evento.patient_id].append(evento)

    resultado = {}
    for paciente, lista in por_paciente.items():
        referencia = ate or instante_mais_recente(lista)
        alinhamento = alinhar_paciente(lista, paciente, referencia, janela)
        if not alinhamento.vazio:
            resultado[paciente] = alinhamento
    return resultado
