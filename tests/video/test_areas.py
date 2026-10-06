"""Gestão e contenção de ROIs poligonais configuráveis."""

from __future__ import annotations

import pytest

from video import AreaCritica, Caixa, ConjuntoAreas, PoligonoInvalido, areas_de_dados


def test_roi_configuravel_em_dict_e_contem_caixa():
    conjunto = ConjuntoAreas(
        tuple(
            areas_de_dados(
                [
                    {
                        "id": "bed_side",
                        "nome": "Borda do leito",
                        "points": [[0, 0], [100, 0], [100, 100], [0, 100]],
                        "area_minima": 0.5,
                    }
                ]
            )
        )
    )
    assert conjunto.por_id("bed_side") is not None
    assert conjunto.contem(Caixa(10, 10, 90, 90))["bed_side"].dentro


def test_escalonamento_rois_com_resolucao():
    area = AreaCritica(
        id="r",
        nome="ROI",
        pontos=((0, 0), (100, 0), (100, 100), (0, 100)),
        largura_ref=100,
        altura_ref=100,
    )
    escalada = area.escala_para(200, 300)
    assert escalada.pontos == ((0.0, 0.0), (200.0, 0.0), (200.0, 300.0), (0.0, 300.0))


def test_poligono_invalido_e_recusado():
    with pytest.raises(PoligonoInvalido):
        AreaCritica(id="ruim", nome="inválido", pontos=((0, 0), (1, 1)))


def test_area_filtra_por_classe():
    area = AreaCritica(
        id="person-only",
        nome="Pessoas",
        pontos=((0, 0), (100, 0), (100, 100), (0, 100)),
        classe_filtro="person",
    )
    assert area.aceita_classe("person")
    assert not area.aceita_classe("chair")
