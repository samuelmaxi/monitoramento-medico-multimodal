"""Presença indevida em área crítica a partir dos eventos da US07.

A US07 publica ``entrada_area_critica``/``saida_area_critica`` por objeto
(``track_id`` do ByteTrack, ou a classe sem rastreamento). Aqui cada entrada é
pareada com a saída seguinte do mesmo objeto na mesma área; se a classe é
proibida naquela área pelo perfil e a permanência passou de ``janela_s``, vira
``presenca_indevida_area_critica``, citando os dois eventos de origem em
``related_event_ids``.

Uma entrada sem saída fica aberta até o fim do vídeo (``fim_video_s``); sem
essa duração, até o último evento conhecido.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import timedelta

from contratos import EventoAchado

from .config import Geral, RegraArea
from .modelos import Desvio

PRESENCA = "presenca_indevida_area_critica"
_TRANSICOES = {"entrada_area_critica", "saida_area_critica"}


def _n(valor: float, casas: int = 1) -> str:
    return f"{valor:.{casas}f}".replace(".", ",")


def _tempo(evento: EventoAchado) -> float:
    return float(evento.evidence.features["tempo_video_s"])


def _chave(evento: EventoAchado) -> tuple[str, str | int]:
    features = evento.evidence.features
    objeto = features.get("track_id")
    return (
        str(features["area_critica_id"]),
        objeto if objeto is not None else str(features["classe"]),
    )


def pares_de_presenca(
    eventos: Iterable[EventoAchado],
) -> list[tuple[EventoAchado, EventoAchado | None]]:
    """``(entrada, saida)`` por objeto e área, na ordem do vídeo; ``saida`` None se não houve."""
    abertos: dict[tuple[str, str | int], EventoAchado] = {}
    pares: list[tuple[EventoAchado, EventoAchado | None]] = []
    for evento in sorted((e for e in eventos if e.event_type in _TRANSICOES), key=_tempo):
        chave = _chave(evento)
        if evento.event_type == "entrada_area_critica":
            abertos.setdefault(chave, evento)
        elif chave in abertos:
            pares.append((abertos.pop(chave), evento))
    pares.extend((entrada, None) for entrada in abertos.values())
    return sorted(pares, key=lambda par: _tempo(par[0]))


def desvios_de_areas(
    eventos: Sequence[EventoAchado],
    regras: Sequence[RegraArea],
    geral: Geral,
    *,
    fim_video_s: float | None = None,
) -> list[Desvio]:
    por_area = {regra.area_id: regra for regra in regras}
    transicoes = [e for e in eventos if e.event_type in _TRANSICOES]
    if fim_video_s is None and transicoes:
        fim_video_s = max(_tempo(e) for e in transicoes)
    desvios = []
    for entrada, saida in pares_de_presenca(transicoes):
        features = entrada.evidence.features
        regra = por_area.get(str(features["area_critica_id"]))
        if regra is None or str(features["classe"]) not in regra.classes_proibidas:
            continue
        inicio = _tempo(entrada)
        fim = _tempo(saida) if saida is not None else float(fim_video_s or inicio)
        duracao = fim - inicio
        if duracao < regra.janela_s:
            continue
        nome = str(features.get("area_critica_nome") or regra.area_id)
        ate = f"até {_n(fim)} s" if saida is not None else "até o fim do vídeo"
        confiancas = [entrada.score] + ([saida.score] if saida is not None else [])
        desvios.append(
            Desvio(
                event_type=PRESENCA,
                regra=f"area.{regra.area_id}",
                severidade=regra.severidade,
                score=round(sum(confiancas) / len(confiancas), 4),
                inicio_s=inicio,
                fim_s=fim,
                pico_s=inicio,
                frame_idx=int(features["frame_index"]) if "frame_index" in features else None,
                medida="permanencia_na_area",
                valor=round(duracao, 2),
                limiar=regra.janela_s,
                unidade="segundos",
                janela_s=regra.janela_s,
                resumo=(
                    f"{features['classe']} permaneceu na área crítica '{nome}' por {_n(duracao)} s "
                    f"(de {_n(inicio)} s {ate}), acima do tolerado de {_n(regra.janela_s)} s."
                ),
                features={
                    "classe": features["classe"],
                    "area_critica_id": regra.area_id,
                    "area_critica_nome": nome,
                    "track_id": features.get("track_id"),
                    "saida_observada": saida is not None,
                    **{k: features[k] for k in ("x1", "y1", "x2", "y2") if k in features},
                },
                relacionados=tuple(str(e.event_id) for e in (entrada, saida) if e is not None),
                inicio_utc=entrada.timestamp,
                fim_utc=saida.timestamp
                if saida is not None
                else entrada.timestamp + timedelta(seconds=duracao),
            )
        )
    return desvios
