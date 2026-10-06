"""ROIs (áreas críticas) configuráveis — US07.

Uma :class:`AreaCritica` é um polígono em pixels, com identificador estável. A
configuração vem de fora (JSON/TOML), nunca de código: o operador aponta os
vértices uma vez por câmera e o mesmo vídeo roda em qualquer máquina.

Coordenadas
-----------
Os vértices são ``[x, y]`` em pixels do frame. Para não repetir as coordenadas
por ROI, ``largura_ref``/``altura_ref`` permitem definir as ROIs numa resolução
de referência e escalonar para a resolução real do vídeo (``escalar_para``).

Critério de contenção
---------------------
Por padrão a detecção pertence à área quando ``>= area_minima`` (0,5) da área da
bounding box está dentro do polígono. Ver :func:`video.geometria.fracao_da_caixa_dentro`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .geometria import (
    PoligonoInvalido,
    fracao_da_caixa_dentro,
    ponto_esta_dentro,
    validar_poligono,
)
from .modelos import Caixa, Poligono, Ponto

AREA_MINIMA_PADRAO = 0.5
"""Fração mínima da bounding box dentro da ROI para contar como entrada."""


def _opcional_int(valor: Any) -> int | None:
    return None if valor is None else int(valor)


def _opcional_str(valor: Any) -> str | None:
    return None if valor is None else str(valor)


@dataclass(frozen=True, slots=True)
class AvaliacaoContencao:
    """Resultado da análise de uma detecção contra uma área crítica."""

    dentro: bool
    fracao: float
    criterio: str

    @property
    def detalhe(self) -> dict[str, float | str]:
        return {"fracao": round(self.fracao, 4), "criterio": self.criterio}


@dataclass(frozen=True, slots=True)
class AreaCritica:
    """Polígono de área crítica, com nome legível para a equipe médica."""

    id: str
    nome: str
    pontos: Poligono
    area_minima: float = AREA_MINIMA_PADRAO
    descricao: str = ""
    largura_ref: int | None = None
    altura_ref: int | None = None
    classe_filtro: str | None = None
    """Quando definido, só detecções desta classe entram na área."""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Área crítica precisa de um id")
        object.__setattr__(self, "pontos", validar_poligono(self.pontos))
        if not 0.0 < self.area_minima <= 1.0:
            raise ValueError(
                f"area_minima deve estar em (0, 1], recebeu {self.area_minima}"
            )

    @classmethod
    def de_dict(cls, dados: dict[str, Any]) -> AreaCritica:
        """Constrói a área a partir de um bloco de configuração."""
        try:
            id_area = str(dados["id"])
            nome = str(dados.get("nome") or dados["id"])
            pontos_brutos = dados["points"]
        except KeyError as erro:
            raise ValueError(f"área crítica incompleta, faltando {erro}") from erro

        if not isinstance(pontos_brutos, Sequence) or isinstance(pontos_brutos, str):
            raise ValueError(f"'points' da área {id_area} precisa ser uma lista de [x, y]")
        if len(pontos_brutos) < 3:
            raise PoligonoInvalido(
                f"área '{id_area}' precisa de ao menos 3 vértices, recebeu {len(pontos_brutos)}"
            )

        pontos: list[Ponto] = []
        for indice, vertice in enumerate(pontos_brutos):
            if not isinstance(vertice, Sequence) or len(vertice) != 2:
                raise ValueError(
                    f"vértice {indice} da área '{id_area}' precisa ser [x, y], recebeu {vertice!r}"
                )
            pontos.append((float(vertice[0]), float(vertice[1])))

        return cls(
            id=id_area,
            nome=nome,
            pontos=tuple(pontos),
            area_minima=float(dados.get("area_minima", AREA_MINIMA_PADRAO)),
            descricao=str(dados.get("descricao", "")),
            largura_ref=_opcional_int(dados.get("largura_ref")),
            altura_ref=_opcional_int(dados.get("altura_ref")),
            classe_filtro=_opcional_str(dados.get("classe_filtro")),
        )

    def para_dict(self) -> dict[str, Any]:
        """Forma serializável, com o formato aceito por :meth:`de_dict`."""
        return {
            "id": self.id,
            "nome": self.nome,
            "points": [list(p) for p in self.pontos],
            "area_minima": self.area_minima,
            "descricao": self.descricao,
            "largura_ref": self.largura_ref,
            "altura_ref": self.altura_ref,
            "classe_filtro": self.classe_filtro,
        }

    def escala_para(self, largura: int, altura: int) -> AreaCritica:
        """Devolve a área com os vértices reescalados para a resolução real.

        Só usa ``largura_ref``/``altura_ref`` quando ambos estiverem definidos;
        caso contrário a área é devolvida intacta (coordenadas já no frame).
        """
        if self.largura_ref is None or self.altura_ref is None:
            return self
        if self.largura_ref <= 0 or self.altura_ref <= 0:
            raise ValueError(
                f"resolução de referência inválida em '{self.id}': "
                f"{self.largura_ref}x{self.altura_ref}"
            )
        escala_x = largura / self.largura_ref
        escala_y = altura / self.altura_ref
        pontos = tuple((x * escala_x, y * escala_y) for x, y in self.pontos)
        return AreaCritica(
            id=self.id,
            nome=self.nome,
            pontos=pontos,
            area_minima=self.area_minima,
            descricao=self.descricao,
            largura_ref=self.largura_ref,
            altura_ref=self.altura_ref,
            classe_filtro=self.classe_filtro,
        )

    def contem(
        self, caixa: Caixa, *, criterio: str = "fracao"
    ) -> AvaliacaoContencao:
        """Avalia uma bounding box contra a área crítica.

        Args:
            caixa: bounding box em pixels do frame.
            criterio: ``"fracao"`` (padrão) usa a fração da área da caixa dentro
                do polígono; ``"centro"`` testa apenas o centro da caixa. O modo
                ``"centro"`` é mais tolerante a ruído de caixa, mas perde a
                informação de objects grandes parcialmente dentro da área.

        Returns:
            :class:`AvaliacaoContencao` com ``dentro``, ``fracao`` (0-1) e o
            critério usado.
        """
        if criterio == "centro":
            dentro = ponto_esta_dentro(caixa.centro, self.pontos)
            fracao = 1.0 if dentro else 0.0
        elif criterio == "fracao":
            fracao = fracao_da_caixa_dentro(caixa, self.pontos)
            dentro = fracao >= self.area_minima
        else:
            raise ValueError(
                f"critério de contenção desconhecido: {criterio!r} (use 'fracao' ou 'centro')"
            )
        return AvaliacaoContencao(dentro=dentro, fracao=fracao, criterio=criterio)

    def aceita_classe(self, nome_classe: str) -> bool:
        """Se a área filtra por classe, verifica se a detecção passa."""
        return self.classe_filtro is None or self.classe_filtro == nome_classe


def carregar_areas(caminho: str | Path) -> list[AreaCritica]:
    """Lê as áreas críticas de um JSON.

    Formatos aceitos: ``{"areas": [...]}``, ``{"areas_criticas": [...]}`` ou uma
    lista direta de áreas.
    """
    dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    return areas_de_dados(dados)


def areas_de_dados(dados: Any) -> list[AreaCritica]:
    """Converte o JSON de áreas (em qualquer formato aceito) em objetos."""
    if isinstance(dados, list):
        brutos: Iterable[Any] = dados
    elif isinstance(dados, dict):
        brutos = dados.get("areas") or dados.get("areas_criticas") or []
    else:
        raise ValueError(f"formato de áreas não reconhecido: {type(dados).__name__}")

    areas = [AreaCritica.de_dict(item) for item in brutos]
    ids = [area.id for area in areas]
    duplicados = {i for i in ids if ids.count(i) > 1}
    if duplicados:
        raise ValueError(f"ids de área críticos duplicados: {sorted(duplicados)}")
    return areas


@dataclass(frozen=True, slots=True)
class ConjuntoAreas:
    """Áreas críticas de uma fonte, já escaladas para a resolução do vídeo."""

    areas: tuple[AreaCritica, ...] = field(default_factory=tuple)

    def __iter__(self):
        return iter(self.areas)

    def __len__(self) -> int:
        return len(self.areas)

    @property
    def vazio(self) -> bool:
        return not self.areas

    def por_id(self, id_area: str) -> AreaCritica | None:
        return next((a for a in self.areas if a.id == id_area), None)

    def para_escala(self, largura: int, altura: int) -> ConjuntoAreas:
        return ConjuntoAreas(tuple(a.escala_para(largura, altura) for a in self.areas))

    @classmethod
    def de_arquivo(cls, caminho: str | Path) -> ConjuntoAreas:
        return cls(tuple(carregar_areas(caminho)))

    def contem(
        self, caixa: Caixa, *, criterio: str = "fracao"
    ) -> dict[str, AvaliacaoContencao]:
        """Avalia a caixa contra todas as áreas, devolvendo por id."""
        return {area.id: area.contem(caixa, criterio=criterio) for area in self.areas}


__all__ = [
    "AREA_MINIMA_PADRAO",
    "AreaCritica",
    "AvaliacaoContencao",
    "ConjuntoAreas",
    "areas_de_dados",
    "carregar_areas",
]
