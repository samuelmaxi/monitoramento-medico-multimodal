"""Registro de enfermagem do MIMIC-IV Clinical Database Demo 2.2 como série de movimentação.

O ``icu/chartevents`` tem três itens de mobilidade, registrados pela
enfermagem ao longo de toda a estadia na UTI:

=========  ==========  =====================================================
itemid     item        uso
=========  ==========  =====================================================
224093     Position    posição atual (``Supine``, ``Left Side``, ...)
224082     Turn        reposicionamento (``Side to Side``, ``Logroll``, ...)
224084     Activity    atividade (``Chair``, ``Ambulate``, ``Bedrest``, ...)
=========  ==========  =====================================================

É a fonte de séries longas (dias) da US17: imobilidade, reposicionamentos
por hora e baseline. Registro não é sensor: a ausência de registro não prova
imobilidade, por isso a série respeita ``lacuna_max_min``. O demo não tem
quedas registradas.

O MIMIC desloca as datas para o futuro (2110-2210); elas ficam como estão, em UTC.
Mesmo arquivo que a US15 lê (``sinais/mimic.py``), com outros itens.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pandas as pd

from .modelos import Observacao, Posicao

DIRETORIO = Path("dados/mimic-iv-demo")
URL_BASE = "https://physionet.org/files/mimic-iv-demo/2.2/icu"

ITENS = {224093: "position", 224082: "turn", 224084: "activity"}

_POSICOES = {
    "Supine": Posicao.SUPINO,
    "Back": Posicao.SUPINO,
    "Reverse T-berg": Posicao.SUPINO,
    "Left Side": Posicao.LATERAL_ESQ,
    "Right Side": Posicao.LATERAL_DIR,
    "Prone": Posicao.PRONO,
    "Sitting": Posicao.SENTADO,
    "Rotating": Posicao.ROTATIVO,
}

_REPOSICIONAMENTOS = ("Side to Side", "Turns by Self", "Logroll", "Per hospital policy")

_ATIVIDADES = {
    "Chair": Posicao.FORA_DO_LEITO,
    "Commode": Posicao.FORA_DO_LEITO,
    "Ambulate": Posicao.FORA_DO_LEITO,
    "Dangle": Posicao.SENTADO,  # sentado na beira do leito, pés para fora
    "Bed as Chair": Posicao.SENTADO,
}

Chave = tuple[str, str, str]
"""``(subject_id, hadm_id, stay_id)`` como texto."""


def _observacao(item: str, valor: str, instante) -> Observacao | None:
    fonte = f"mimic_{item}"
    evidencia = {"item": item, "valor": valor}
    if item in ("position", "turn") and valor in _POSICOES:
        return Observacao(instante, _POSICOES[valor], fonte, evidencia=evidencia)
    if item == "turn" and valor.startswith(_REPOSICIONAMENTOS):
        return Observacao(instante, Posicao.DESCONHECIDA, fonte, mudanca=True, evidencia=evidencia)
    if item == "activity" and valor in _ATIVIDADES:
        return Observacao(instante, _ATIVIDADES[valor], fonte, evidencia=evidencia)
    return None


def observacoes_de_dataframe(df: pd.DataFrame) -> dict[Chave, list[Observacao]]:
    """Linhas no formato ``chartevents`` → observações por estadia, em ordem de tempo.

    Itens fora de :data:`ITENS` e valores sem mapeamento (``Bedrest``,
    ``Activity Tolerance``...) são ignorados.
    """
    df = df[df.itemid.isin(ITENS)]
    series: dict[Chave, list[Observacao]] = defaultdict(list)
    instantes = pd.to_datetime(df.charttime, utc=True)
    for linha, instante in zip(df.itertuples(index=False), instantes, strict=True):
        valor = str(linha.value).strip()
        o = _observacao(ITENS[int(linha.itemid)], valor, instante.to_pydatetime())
        if o is not None:
            chave = (str(linha.subject_id), str(linha.hadm_id), str(linha.stay_id))
            series[chave].append(o)
    return {k: sorted(v, key=lambda o: o.instante) for k, v in series.items()}


def baixar(diretorio: str | Path = DIRETORIO) -> Path:
    """Baixa o ``chartevents`` do demo (acesso aberto, sem credencial)."""
    import urllib.request

    destino = Path(diretorio) / "chartevents.csv.gz"
    if not destino.exists():
        destino.parent.mkdir(parents=True, exist_ok=True)
        parcial = destino.with_suffix(".parcial")
        urllib.request.urlretrieve(f"{URL_BASE}/chartevents.csv.gz", parcial)
        parcial.rename(destino)
    return destino


def carregar(diretorio: str | Path = DIRETORIO) -> dict[Chave, list[Observacao]]:
    """Lê só as colunas e os itens de mobilidade do ``chartevents.csv.gz``."""
    caminho = Path(diretorio) / "chartevents.csv.gz"
    if not caminho.is_file():
        raise FileNotFoundError(
            f"{caminho} não encontrado. Baixe com: python -m movimentacao.run baixar"
        )
    colunas = ["subject_id", "hadm_id", "stay_id", "charttime", "itemid", "value"]
    partes = [
        parte[parte.itemid.isin(ITENS)]
        for parte in pd.read_csv(caminho, usecols=colunas, chunksize=200_000)
    ]
    return observacoes_de_dataframe(pd.concat(partes, ignore_index=True))


__all__ = ["DIRETORIO", "ITENS", "baixar", "carregar", "observacoes_de_dataframe"]
