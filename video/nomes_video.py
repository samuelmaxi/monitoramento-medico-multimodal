"""Parser testável dos nomes de arquivo de vídeo da US07.

Os nomes dos vídeos do projeto seguem uma convenção com tokens separados por
``_`` (por exemplo ``B_D_0001.mp4``, ``B_N_87_resized.mp4``). Este módulo traduz
apenas o que foi **explicitamente informado** pelo responsável pelo projeto e
preserva o resto como token desconhecido — não inventa significado.

Convenções confirmadas (informadas, não inferidas)
--------------------------------------------------
- ``F``  → condição **queda** (*fall*).
- ``NF`` → condição **sem queda** (*no fall*).
- ``raw`` → vídeo original, o mais próximo possível da gravação.
- ``mask`` → vídeo com a pessoa sobre fundo preto.
- ``B``  → cenário **cama**.
- ``C``  → cenário **cadeira**.
- ``S``  → cenário **pessoa em pé**.
- ``_resized`` → vídeo redimensionado (derivado de outro).

Tokens ainda não documentados
-----------------------------
O token ``D`` em ``B_D_0001`` e o token ``N`` em ``B_N_100`` **não** têm
significado confirmado. Em particular, ``N`` **não** é tratado como ``NF``: a
convenção de condição usa o token exato ``NF``. Enquanto o significado não for
confirmado, ``D`` e ``N`` entram em ``tokens_desconhecidos`` e o nome é marcado
como ``requer_revisao``.

A ambiguidade é tratada como pendência: se um nome apresentar dois cenários,
duas condições ou duas modalidades diferentes, o parser não escolhe um valor —
registra a ambiguidade e devolve ``requer_revisao=True``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

TOKENS_CENARIO: dict[str, str] = {
    "B": "cama",
    "C": "cadeira",
    "S": "em_pe",
}
"""Tokens de cenário confirmados — preservados como **metadados**, não classes."""

TOKENS_CONDICAO: dict[str, str] = {
    "F": "queda",
    "NF": "sem_queda",
}
"""Tokens de condição (temporais) confirmados. Uma imagem isolada não comprova queda."""

TOKENS_MODALIDADE: dict[str, str] = {
    "raw": "raw",
    "mask": "mask",
}
"""Origem dos pixels: contexto original (``raw``) ou pessoa sobre fundo preto (``mask``)."""

TOKEN_REDIMENSIONADO = "resized"
"""Sufixo ``_resized``: vídeo derivado, redimensionado do original."""

_EXTENSOES_VIDEO = frozenset({".mp4", ".avi", ".mov", ".mkv", ".m4v", ".webm", ".mpg", ".mpeg"})
_PADRAO_NUMERICO = re.compile(r"^\d+$")


def extensao_video_suportada(sufixo: str) -> bool:
    """``True`` quando a extensão (com ponto) é de um vídeo tratável pelo OpenCV."""
    return sufixo.lower() in _EXTENSOES_VIDEO


@dataclass(frozen=True, slots=True)
class NomeVideo:
    """Metadados extraídos do nome do arquivo.

    Todos os campos que não puderam ser confirmados ficam em ``None`` ou em
    ``tokens_desconhecidos`` — nunca em um palpite.
    """

    arquivo: str
    stem: str
    tokens: tuple[str, ...]
    cenario: str | None
    cenario_token: str | None
    condicao: str | None
    condicao_token: str | None
    modalidade: str | None
    modalidade_token: str | None
    redimensionado: bool
    identificador: str | None
    tokens_desconhecidos: tuple[str, ...]
    ambiguidades: tuple[str, ...]
    requer_revisao: bool
    chave_grupo: str

    @property
    def grupo(self) -> str | None:
        """Grupo de cenário confirmado, usado em relatórios e estratificação."""
        return self.cenario

    def para_dict(self) -> dict[str, object]:
        return {
            "arquivo": self.arquivo,
            "stem": self.stem,
            "cenario": self.cenario,
            "cenario_token": self.cenario_token,
            "condicao": self.condicao,
            "condicao_token": self.condicao_token,
            "modalidade": self.modalidade,
            "redimensionado": self.redimensionado,
            "identificador": self.identificador,
            "tokens_desconhecidos": list(self.tokens_desconhecidos),
            "ambiguidades": list(self.ambiguidades),
            "requer_revisao": self.requer_revisao,
            "chave_grupo": self.chave_grupo,
        }


def _unico(
    valores: Iterable[tuple[str, str]],
) -> tuple[tuple[str | None, str | None], tuple[str, ...]]:
    """Resolve um campo que só pode ter um valor.

    Returns:
        ``((valor, token), pendências)``. Com dois valores distintos, o valor é
        ``None`` e a ambiguidade é registrada em vez de forçar uma escolha.
    """
    vistos = list(valores)
    if not vistos:
        return (None, None), ()
    distintos = {valor for valor, _ in vistos}
    if len(distintos) > 1:
        return (None, None), (tuple(token for _, token in vistos),)
    valor, token = vistos[0]
    return (valor, token), ()


def parsear_nome(arquivo: str | Path) -> NomeVideo:
    """Interpreta o nome de um arquivo de vídeo e devolve seus metadados.

    Args:
        arquivo: caminho ou nome do arquivo (o diretório é ignorado).

    Returns:
        :class:`NomeVideo` com os metadados confirmados e os tokens
        desconhecidos/ambíguos preservados.
    """
    caminho = Path(arquivo)
    stem = caminho.stem
    nome = caminho.name
    tokens = tuple(t for t in stem.split("_") if t != "")

    cenarios: list[tuple[str, str]] = []
    condicoes: list[tuple[str, str]] = []
    modalidades: list[tuple[str, str]] = []
    redimensionado = False
    identificador: str | None = None
    desconhecidos: list[str] = []

    for token in tokens:
        if token in TOKENS_CENARIO:
            cenarios.append((TOKENS_CENARIO[token], token))
            continue
        if token in TOKENS_CONDICAO:
            condicoes.append((TOKENS_CONDICAO[token], token))
            continue
        minusculo = token.lower()
        if minusculo in TOKENS_MODALIDADE:
            modalidades.append((TOKENS_MODALIDADE[minusculo], token))
            continue
        if minusculo == TOKEN_REDIMENSIONADO:
            redimensionado = True
            continue
        if _PADRAO_NUMERICO.match(token):
            if identificador is None:
                identificador = token
            else:
                desconhecidos.append(token)
            continue
        desconhecidos.append(token)

    (cenario, cenario_token), amb_cenario = _unico(cenarios)
    (condicao, condicao_token), amb_condicao = _unico(condicoes)
    (modalidade, modalidade_token), amb_modalidade = _unico(modalidades)

    ambiguidades = tuple(
        f"{rotulo}: {sorted(pendencia)}"
        for rotulo, pendencia in (
            ("cenario", amb_cenario),
            ("condicao", amb_condicao),
            ("modalidade", amb_modalidade),
        )
        if pendencia
    )

    tokens_desconhecidos = tuple(desconhecidos)
    irrecuperavel = tuple(t for t in tokens_desconhecidos if not _PADRAO_NUMERICO.match(t))
    requer_revisao = bool(ambiguidades) or bool(irrecuperavel)

    chave_grupo = _chave_grupo(tokens)

    return NomeVideo(
        arquivo=nome,
        stem=stem,
        tokens=tokens,
        cenario=cenario,
        cenario_token=cenario_token,
        condicao=condicao,
        condicao_token=condicao_token,
        modalidade=modalidade,
        modalidade_token=modalidade_token,
        redimensionado=redimensionado,
        identificador=identificador,
        tokens_desconhecidos=tokens_desconhecidos,
        ambiguidades=ambiguidades,
        requer_revisao=requer_revisao,
        chave_grupo=chave_grupo,
    )


def _chave_grupo(tokens: tuple[str, ...]) -> str:
    """Normaliza o nome removendo tokens derivados (modalidade/``resized``).

    Vídeos ``raw``/``mask``/``_resized`` do mesmo original compartilham a chave,
    o que impede que frames quase idênticos caiam em conjuntos diferentes.
    """
    derivados = {TOKEN_REDIMENSIONADO, *(m.lower() for m in TOKENS_MODALIDADE)}
    restantes = [t for t in tokens if t.lower() not in derivados]
    return "_".join(restantes) if restantes else Path("_".join(tokens)).stem


__all__ = [
    "TOKENS_CENARIO",
    "TOKENS_CONDICAO",
    "TOKENS_MODALIDADE",
    "TOKEN_REDIMENSIONADO",
    "NomeVideo",
    "extensao_video_suportada",
    "parsear_nome",
]
