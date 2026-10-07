"""Primitivas geométricas: IoU e interseção caixa/polígono (US07)."""

from __future__ import annotations

import math

import pytest

from video.geometria import (
    PoligonoInvalido,
    area_interseccao_caixa_poligono,
    area_poligono,
    fracao_da_caixa_dentro,
    iou,
    ponto_esta_dentro,
    validar_poligono,
)
from video.modelos import Caixa

QUADRADO = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))


def caixa(x1: float, y1: float, x2: float, y2: float) -> Caixa:
    return Caixa(x1, y1, x2, y2)


class TestIoU:
    def test_iou_de_caixas_identicas_e_um(self):
        alvo = caixa(10, 10, 20, 20)
        assert iou(alvo, alvo) == pytest.approx(1.0)

    def test_iou_de_caixas_disjuntas_e_zero(self):
        assert iou(caixa(0, 0, 10, 10), caixa(50, 50, 60, 60)) == 0.0

    def test_iou_de_caixas_que_so_se_tocam_e_zero(self):
        """Encostar na borda não é sobrepor: área de interseção zero."""
        assert iou(caixa(0, 0, 10, 10), caixa(10, 0, 20, 10)) == 0.0

    def test_iou_parcial_entre_caixas_sobrepostas(self):
        # interseção 100 (10x10), união 100 + 100 - 100 = ... calculada abaixo
        valor = iou(caixa(0, 0, 10, 10), caixa(5, 0, 15, 10))
        # inter = 50, união = 100 + 100 - 50 = 150 → 1/3
        assert valor == pytest.approx(1 / 3)

    def test_iou_calculado_por_formula(self):
        a = caixa(0, 0, 10, 10)
        b = caixa(2, 2, 12, 12)
        inter = 8 * 8
        uniao = a.area + b.area - inter
        assert iou(a, b) == pytest.approx(inter / uniao)

    @pytest.mark.parametrize(
        ("a", "b", "esperado_minimo"),
        [
            ((0, 0, 10, 10), (0, 0, 10, 10), 0.5),
            ((0, 0, 10, 10), (3, 0, 13, 10), 0.5),
        ],
    )
    def test_iou_atinge_o_criterio_de_acerto_de_0_5(self, a, b, esperado_minimo):
        """O critério de acerto da US07 é IoU >= 0,50."""
        valor = iou(caixa(*a), caixa(*b))
        assert (valor >= 0.5) is (esperado_minimo >= 0.5)

    def test_caixa_sobreposta_parcialmente_fica_abaixo_de_0_5(self):
        # interseção 2x10 = 20; união 100 + 100 - 20 = 180 → 0,111
        assert iou(caixa(0, 0, 10, 10), caixa(8, 0, 18, 10)) < 0.5

    def test_caixa_de_area_zero_nao_quebra(self):
        degenerada = Caixa(5, 5, 5, 5)
        assert iou(degenerada, caixa(0, 0, 10, 10)) == 0.0

    def test_caixa_invertida_e_recusada(self):
        with pytest.raises(ValueError, match="Caixa invertida"):
            Caixa(10, 0, 0, 10)

    def test_caixa_com_infinito_e_recusada(self):
        with pytest.raises(ValueError, match="não finita"):
            Caixa(0, 0, math.inf, 10)


class TestPontoEmPoligono:
    def test_ponto_interior_esta_dentro(self):
        assert ponto_esta_dentro((5, 5), QUADRADO)

    def test_ponto_externo_esta_fora(self):
        assert not ponto_esta_dentro((15, 5), QUADRADO)

    def test_ponto_acima_do_quadrado_esta_fora(self):
        assert not ponto_esta_dentro((5, -1), QUADRADO)

    def test_ponto_na_borda_vertical_conta_como_dentro(self):
        assert ponto_esta_dentro((0, 5), QUADRADO)

    def test_ponto_no_vertice_conta_como_dentro(self):
        assert ponto_esta_dentro((0, 0), QUADRADO)

    def test_ponto_na_borda_nao_conta_quando_borda_excluida(self):
        assert not ponto_esta_dentro((10, 5), QUADRADO, incluir_borda=False)

    def test_ponto_em_poligono_concavo(self):
        # "L"-shaped polygon: (0,0) (10,0) (10,4) (4,4) (4,10) (0,10)
        em_l = ((0.0, 0.0), (10.0, 0.0), (10.0, 4.0), (4.0, 4.0), (4.0, 10.0), (0.0, 10.0))
        assert ponto_esta_dentro((2, 2), em_l)
        assert not ponto_esta_dentro((8, 8), em_l)

    def test_poligono_concavo_tem_area_correta(self):
        em_l = ((0.0, 0.0), (10.0, 0.0), (10.0, 4.0), (4.0, 4.0), (4.0, 10.0), (0.0, 10.0))
        # 10x10 menos o canto 6x6 recortado = 100 - 36 = 64
        assert area_poligono(em_l) == pytest.approx(64.0)


class TestFracaoDaCaixaDentro:
    def test_caixa_totalmente_dentro_tem_fracao_um(self):
        assert fracao_da_caixa_dentro(caixa(2, 2, 8, 8), QUADRADO) == pytest.approx(1.0)

    def test_caixa_totalmente_fora_tem_fracao_zero(self):
        assert fracao_da_caixa_dentro(caixa(20, 20, 30, 30), QUADRADO) == 0.0

    def test_caixa_que_cruza_a_borda_tem_fracao_parcial(self):
        # caixa 10x10 com x de 2 a 12 → 8 pixels dentro dos 10 → 0,8
        assert fracao_da_caixa_dentro(caixa(2, 0, 12, 10), QUADRADO) == pytest.approx(0.8)

    def test_area_de_interseccao_igual_a_area_da_caixa(self):
        assert area_interseccao_caixa_poligono(caixa(2, 2, 4, 4), QUADRADO) == pytest.approx(4.0)

    def test_interseccao_com_poligono_concavo(self):
        em_l = ((0.0, 0.0), (10.0, 0.0), (10.0, 4.0), (4.0, 4.0), (4.0, 10.0), (0.0, 10.0))
        # caixa na perna horizontal superior: 0..10 x 0..4 → área 40
        assert area_interseccao_caixa_poligono(caixa(0, 0, 10, 4), em_l) == pytest.approx(40.0)
        # caixa quadrada 0..10 x 0..10 → 100 - 36 = 64
        assert area_interseccao_caixa_poligono(caixa(0, 0, 10, 10), em_l) == pytest.approx(64.0)

    def test_caixa_degenerada_devolve_zero(self):
        assert fracao_da_caixa_dentro(Caixa(5, 5, 5, 5), QUADRADO) == 0.0

    def test_fracao_nunca_excede_um(self):
        for x2 in (3.0, 10.0, 20.0):
            assert fracao_da_caixa_dentro(caixa(0, 0, x2, 10), QUADRADO) <= 1.0


class TestValidarPoligono:
    def test_poligono_valido_passa(self):
        assert validar_poligono(QUADRADO) == QUADRADO

    def test_poligono_com_dois_vertices_e_recusado(self):
        with pytest.raises(PoligonoInvalido, match="ao menos 3"):
            validar_poligono(((0.0, 0.0), (1.0, 1.0)))

    def test_poligono_vazio_e_recusado(self):
        with pytest.raises(PoligonoInvalido):
            validar_poligono(())

    def test_poligono_colinear_e_recusado(self):
        with pytest.raises(PoligonoInvalido, match="área nula"):
            validar_poligono(((0.0, 0.0), (1.0, 1.0), (2.0, 2.0)))

    def test_poligono_com_vertice_repetido_e_recusado(self):
        with pytest.raises(PoligonoInvalido, match="área nula"):
            validar_poligono(((0.0, 0.0), (0.0, 0.0), (0.0, 0.0)))

    def test_poligono_com_coordenada_infinita_e_recusado(self):
        with pytest.raises(PoligonoInvalido, match="não finito"):
            validar_poligono(((0.0, 0.0), (math.inf, 0.0), (0.0, 10.0)))
