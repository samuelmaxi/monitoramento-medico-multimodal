"""Avaliação da US17 em sequências rotuladas.

Duas frentes, contadas com a mesma :class:`~video.desvios.avaliacao.Contagem`
da US08 (unidade = clipe ou caso):

1. **Quedas em vídeo real** (meta do projeto: recall ≥ 0,80). Clipes de leito
   do FallVision rotulados em ``data/eval/us17/quedas.csv``. Calibração e
   teste separados pela paridade do MD5 do ``grupo`` (mesma regra da US08,
   ``scripts/avaliar_us08.py``); clipes que são o mesmo vídeo reencodado têm o
   mesmo grupo, para não cair um em cada lado.
2. **Casos injetados** sobre séries reais do MIMIC (:mod:`.injecao`), para os
   cinco tipos.

    recall = unidades positivas com achado / unidades positivas
    alarmes falsos = unidades de controle com achado / unidades de controle
"""

from __future__ import annotations

import csv
import hashlib
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from video.desvios.avaliacao import Contagem

from .config import ConfigMovimentacao
from .injecao import Caso
from .modelos import Anomalia
from .regras import detectar
from .video import REGRAS_QUEDA_PADRAO, CaixasDoVideo, observacoes_de_caixas

ROTULOS = Path("data/eval/us17/quedas.csv")
TOLERANCIA = timedelta(minutes=5)
_INICIO_CLIPE = datetime(2026, 1, 1, 12, tzinfo=UTC)
"""Instante fictício do quadro 0: só a queda importa, e queda é alta em qualquer período."""


@dataclass(frozen=True)
class ClipeRotulado:
    video_id: str
    queda: bool
    grupo: str
    particao: str


def particao(grupo: str) -> str:
    paridade = int(hashlib.md5(grupo.encode("utf-8")).hexdigest(), 16) % 2
    return "calibracao" if paridade == 0 else "teste"


def ler_rotulos(caminho: str | Path = ROTULOS) -> list[ClipeRotulado]:
    with Path(caminho).open(encoding="utf-8", newline="") as fh:
        return [
            ClipeRotulado(
                video_id=linha["video_id"],
                queda=linha["queda"].strip() == "1",
                grupo=linha["grupo"],
                particao=particao(linha["grupo"]),
            )
            for linha in csv.DictReader(fh)
        ]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de confiança de Wilson (95%) para a proporção k/n."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    centro = (p + z * z / (2 * n)) / (1 + z * z / n)
    meia = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centro - meia), min(1.0, centro + meia))


# --------------------------------------------------------------------------- #
# Quedas em vídeo
# --------------------------------------------------------------------------- #


def quedas_do_clipe(
    caixas: CaixasDoVideo,
    cfg: ConfigMovimentacao,
    regras: Iterable[str] = REGRAS_QUEDA_PADRAO,
) -> list[Anomalia]:
    """Quedas do clipe pelas regras de caixa pedidas, já fundidas pela regra de queda."""
    observacoes = observacoes_de_caixas(caixas, _INICIO_CLIPE, cfg.video, regras)
    return detectar(observacoes, cfg, tipos=["queda"])


def avaliar_clipes(
    rotulos: Iterable[ClipeRotulado], quedas: Mapping[str, Sequence[object]]
) -> dict[str, Contagem]:
    """Contagem por partição (e ``"total"``); clipe sem entrada em ``quedas`` fica de fora."""
    resultado: dict[str, Contagem] = {"total": Contagem()}
    for r in rotulos:
        if r.video_id not in quedas:
            continue
        achados = quedas[r.video_id]
        for c in (resultado["total"], resultado.setdefault(r.particao, Contagem())):
            c.eventos += len(achados)
            if r.queda:
                c.unidades_desvio += 1
                c.eventos_corretos += len(achados)
                if achados:
                    c.unidades_desvio_detectadas += 1
                else:
                    c.perdidas.append(r.video_id)
            else:
                c.unidades_normais += 1
                if achados:
                    c.unidades_normais_com_evento += 1
                    c.alarmes_falsos.append(r.video_id)
    return resultado


# --------------------------------------------------------------------------- #
# Casos injetados
# --------------------------------------------------------------------------- #


def _dispara(caso: Caso, anomalias: Iterable[Anomalia]) -> bool:
    ini, fim = caso.inicio - TOLERANCIA, caso.fim + TOLERANCIA
    for a in anomalias:
        if a.event_type != caso.event_type or not (a.inicio <= fim and a.fim >= ini):
            continue
        # Imobilidade: o trecho real anterior termina exatamente onde a injeção começa.
        if caso.event_type == "imobilidade_prolongada" and a.fim <= caso.inicio:
            continue
        if caso.severidade is None or a.severidade is caso.severidade:
            return True
    return False


def avaliar_casos(casos: Iterable[Caso], cfg: ConfigMovimentacao) -> dict[str, dict[str, Contagem]]:
    """Contagens por ``event_type`` e por cenário (``"total"`` agrega o tipo)."""
    resultado: dict[str, dict[str, Contagem]] = {}
    for caso in casos:
        anomalias = detectar(caso.observacoes, cfg, tipos=[caso.event_type])
        achou = _dispara(caso, anomalias)
        por_tipo = resultado.setdefault(caso.event_type, {"total": Contagem()})
        for c in (por_tipo["total"], por_tipo.setdefault(caso.cenario, Contagem())):
            if caso.positivo:
                c.unidades_desvio += 1
                c.unidades_desvio_detectadas += achou
                if not achou:
                    c.perdidas.append(f"{caso.id}: {caso.descricao}")
            else:
                c.unidades_normais += 1
                c.unidades_normais_com_evento += achou
                if achou:
                    c.alarmes_falsos.append(f"{caso.id}: {caso.descricao}")
    return resultado


__all__ = [
    "ROTULOS",
    "ClipeRotulado",
    "avaliar_casos",
    "avaliar_clipes",
    "ler_rotulos",
    "particao",
    "quedas_do_clipe",
    "wilson",
]
