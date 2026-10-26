"""Testes da avaliação de detecção por IoU >= 0,50 (PASCAL VOC / mAP@0.5)."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from video.anotacoes import Anotacao
from video.avaliacao_deteccao import (
    LIMIAR_IOU_PADRAO,
    Predicao,
    Verdade,
    anotacao_para_caixa,
    ap_50,
    avaliar_deteccao,
    carregar_verdades,
    casar_deteccoes,
    ler_predicoes_jsonl,
    predicoes_de_detector,
)
from video.geometria import iou
from video.modelos import Caixa, Deteccao


def _pred(imagem: str, caixa: Caixa, confianca: float = 0.9, class_id: int = 0) -> Predicao:
    return Predicao(imagem=imagem, class_id=class_id, confianca=confianca, caixa=caixa)


def _verd(imagem: str, caixa: Caixa, class_id: int = 0) -> Verdade:
    return Verdade(imagem=imagem, class_id=class_id, caixa=caixa)


class TestCasamentoIoU:
    def test_iou_exatamente_meio_casa(self):
        # (0,0,2,2) vs (0,0,2,1): inter 2, uniao 4 -> IoU 0,5 (limite inclusive).
        referencia = Caixa(0, 0, 2, 2)
        candidata = Caixa(0, 0, 2, 1)
        assert iou(referencia, candidata) == pytest.approx(0.5)
        casamentos = casar_deteccoes([_pred("a", candidata)], [_verd("a", referencia)])
        assert casamentos[0].verdadeiro_positivo

    def test_iou_abaixo_do_limiar_nao_casa(self):
        referencia = Caixa(0, 0, 2, 2)
        candidata = Caixa(0, 0, 2, 0.9)
        assert iou(referencia, candidata) < LIMIAR_IOU_PADRAO
        casamentos = casar_deteccoes([_pred("a", candidata)], [_verd("a", referencia)])
        assert not casamentos[0].verdadeiro_positivo

    def test_classes_diferentes_nao_casam(self):
        caixa = Caixa(0, 0, 10, 10)
        casamentos = casar_deteccoes([_pred("a", caixa, class_id=1)], [_verd("a", caixa, class_id=0)])
        assert not casamentos[0].verdadeiro_positivo
        resultado = avaliar_deteccao([_pred("a", caixa, class_id=1)], [_verd("a", caixa, class_id=0)])
        assert (resultado.verdadeiros_positivos, resultado.falsos_positivos) == (0, 1)
        assert resultado.falsos_negativos == 1

    def test_uma_verdade_recebe_uma_unica_predicao(self):
        referencia = Caixa(0, 0, 10, 10)
        duplicada = Caixa(0, 0, 10, 9)  # IoU ~0,9 com a verdade
        casamentos = casar_deteccoes(
            [_pred("a", referencia, 0.95), _pred("a", duplicada, 0.5)],
            [_verd("a", referencia)],
        )
        assert sum(c.verdadeiro_positivo for c in casamentos) == 1
        assert casamentos[0].verdadeiro_positivo  # maior confiança fica com a verdade
        assert not casamentos[1].verdadeiro_positivo

    def test_associacao_por_maior_iou(self):
        # Duas verdades; a predição de maior confiança deve pegar a de maior IoU.
        perto = Caixa(0, 0, 10, 10)
        longe = Caixa(100, 100, 120, 120)
        predicao = Caixa(0, 0, 10, 9)
        casamentos = casar_deteccoes([_pred("a", predicao)], [_verd("a", longe), _verd("a", perto)])
        assert casamentos[0].verdadeiro_positivo
        assert casamentos[0].indice_verdade == 1

    def test_verdade_sem_predicao_vira_falso_negativo(self):
        resultado = avaliar_deteccao([], [_verd("a", Caixa(0, 0, 10, 10))])
        assert resultado.falsos_negativos == 1
        assert resultado.verdadeiros_positivos == 0

    def test_limiar_invalido(self):
        with pytest.raises(ValueError):
            casar_deteccoes([], [], limiar_iou=1.5)


class TestAPVOC:
    def test_ap_perfeita(self):
        referencia = Caixa(0, 0, 10, 10)
        preds = [_pred("a", referencia, 0.9), _pred("b", referencia, 0.8)]
        verdades = [_verd("a", referencia), _verd("b", referencia)]
        assert ap_50(casar_deteccoes(preds, verdades), len(verdades)) == pytest.approx(1.0)

    def test_ap_metade_das_verdades(self):
        referencia = Caixa(0, 0, 10, 10)
        preds = [_pred("a", referencia, 0.9)]
        verdades = [_verd("a", referencia), _verd("b", referencia)]
        assert ap_50(casar_deteccoes(preds, verdades), len(verdades)) == pytest.approx(0.5)

    def test_ap_sem_verdades_e_zero(self):
        assert ap_50([], 0) == 0.0

    def test_falso_positivo_apos_todos_os_tp_nao_reduz_ap(self):
        referencia = Caixa(0, 0, 10, 10)
        preds = [_pred("a", referencia, 0.9), _pred("a", Caixa(0, 0, 1, 1), 0.8)]
        verdades = [_verd("a", referencia)]
        assert ap_50(casar_deteccoes(preds, verdades), len(verdades)) == pytest.approx(1.0)

    def test_confianca_maior_prioriza_na_curva(self):
        # A predição correta tem maior confiança que o falso positivo.
        referencia = Caixa(0, 0, 10, 10)
        preds = [
            _pred("a", Caixa(50, 50, 60, 60), 0.95),  # FP primeiro (confiança alta)
            _pred("a", referencia, 0.5),  # TP depois
        ]
        ap = ap_50(casar_deteccoes(preds, [_verd("a", referencia)]), 1)
        assert ap == pytest.approx(0.5)


class TestAvaliarDeteccao:
    def test_relatorio_macro_entre_classes(self):
        caixa = Caixa(0, 0, 10, 10)
        preds = [_pred("a", caixa, class_id=0), _pred("a", caixa, class_id=1)]
        verdades = [_verd("a", caixa, class_id=0), _verd("a", caixa, class_id=1)]
        resultado = avaliar_deteccao(preds, verdades)
        assert resultado.mapa_50 == pytest.approx(1.0)
        assert {m.class_id: m.ap_50 for m in resultado.por_classe} == {0: 1.0, 1: 1.0}
        assert resultado.medido

    def test_classe_sem_predicao_entra_com_ap_zero(self):
        caixa = Caixa(0, 0, 10, 10)
        preds = [_pred("a", caixa, class_id=0)]
        verdades = [_verd("a", caixa, class_id=0), _verd("a", caixa, class_id=2)]
        resultado = avaliar_deteccao(preds, verdades)
        assert resultado.mapa_50 == pytest.approx(0.5)
        classe_2 = next(m for m in resultado.por_classe if m.class_id == 2)
        assert classe_2.ap_50 == 0.0 and classe_2.falsos_negativos == 1

    def test_sem_verdades_nao_medido(self):
        resultado = avaliar_deteccao([_pred("a", Caixa(0, 0, 10, 10))], [])
        assert resultado.mapa_50 is None
        assert not resultado.medido

    def test_para_dict_tem_criterio_e_contagens(self):
        caixa = Caixa(0, 0, 10, 10)
        resultado = avaliar_deteccao([_pred("a", caixa)], [_verd("a", caixa)])
        dados = resultado.para_dict()
        assert "IoU >= 0.5" in dados["criterio"]
        assert dados["mAP@0.5"] == pytest.approx(1.0)
        assert dados["verdadeiros_positivos"] == 1

    def test_salvar_json(self, tmp_path: Path):
        caixa = Caixa(0, 0, 10, 10)
        resultado = avaliar_deteccao([_pred("a", caixa)], [_verd("a", caixa)])
        destino = resultado.salvar_json(tmp_path / "rel" / "aval.json")
        assert json.loads(destino.read_text(encoding="utf-8"))["mAP@0.5"] == pytest.approx(1.0)


class TestConversaoELeitura:
    def test_anotacao_para_caixa_pixels(self):
        anotacao = Anotacao(class_id=0, x_center=0.5, y_center=0.5, largura=0.4, altura=0.2)
        caixa = anotacao_para_caixa(anotacao, largura=100, altura=50)
        assert (caixa.x1, caixa.y1, caixa.x2, caixa.y2) == (30.0, 20.0, 70.0, 30.0)

    def test_anotacao_para_caixa_dimensao_invalida(self):
        anotacao = Anotacao(class_id=0, x_center=0.5, y_center=0.5, largura=0.4, altura=0.2)
        with pytest.raises(ValueError):
            anotacao_para_caixa(anotacao, largura=0, altura=50)

    def test_carregar_verdades(self, tmp_path: Path):
        imagens = tmp_path / "images"
        labels = tmp_path / "labels"
        imagens.mkdir()
        labels.mkdir()
        cv2.imwrite(str(imagens / "a.jpg"), np.zeros((50, 100, 3), dtype=np.uint8))
        (labels / "a.txt").write_text("0 0.5 0.5 0.4 0.2\n", encoding="utf-8")
        (labels / "b.txt").write_text("", encoding="utf-8")  # negativa não tem imagem: ignorada

        verdades = carregar_verdades(imagens, labels)
        assert len(verdades) == 1
        assert verdades[0].imagem == "a"
        assert verdades[0].caixa == Caixa(30.0, 20.0, 70.0, 30.0)

    def test_carregar_verdades_filtra_classe(self, tmp_path: Path):
        imagens = tmp_path / "images"
        labels = tmp_path / "labels"
        imagens.mkdir()
        labels.mkdir()
        cv2.imwrite(str(imagens / "a.jpg"), np.zeros((10, 10, 3), dtype=np.uint8))
        (labels / "a.txt").write_text("0 0.5 0.5 0.2 0.2\n1 0.5 0.5 0.2 0.2\n", encoding="utf-8")
        verdades = carregar_verdades(imagens, labels, classes=(0,))
        assert [v.class_id for v in verdades] == [0]

    def test_ler_predicoes_jsonl_formatos(self, tmp_path: Path):
        caminho = tmp_path / "pred.jsonl"
        linhas = [
            {"imagem": "a.jpg", "class_id": 0, "confianca": 0.9, "caixa": [0, 0, 10, 10]},
            {"imagem": "b", "class_id": 1, "x1": 1, "y1": 2, "x2": 3, "y2": 4},
        ]
        caminho.write_text("\n".join(json.dumps(x) for x in linhas), encoding="utf-8")
        predicoes = ler_predicoes_jsonl(caminho)
        assert predicoes[0].imagem == "a" and predicoes[0].confianca == pytest.approx(0.9)
        assert predicoes[1].imagem == "b" and predicoes[1].caixa == Caixa(1, 2, 3, 4)
        assert predicoes[1].confianca == pytest.approx(1.0)  # default

    def test_ler_predicoes_jsonl_campos_ausentes(self, tmp_path: Path):
        caminho = tmp_path / "pred.jsonl"
        caminho.write_text('{"imagem": "a"}\n', encoding="utf-8")
        with pytest.raises(ValueError):
            ler_predicoes_jsonl(caminho)


class _DetectorFalso:
    """Detector duck-typed: devolve sempre a mesma detecção, sem Ultralytics."""

    def __init__(self, class_id: int = 0) -> None:
        self._class_id = class_id

    def detectar(self, imagem, *, indice_quadro: int, tempo_s: float):
        return [
            Deteccao(
                class_id=self._class_id,
                class_name="person",
                confianca=0.9,
                caixa=Caixa(0, 0, 10, 10),
                frame_index=indice_quadro,
                tempo_s=tempo_s,
            )
        ]


class TestPredicoesDeDetector:
    def test_gera_predicoes_de_pasta(self, tmp_path: Path):
        imagens = tmp_path / "images"
        imagens.mkdir()
        cv2.imwrite(str(imagens / "a.jpg"), np.zeros((10, 10, 3), dtype=np.uint8))
        cv2.imwrite(str(imagens / "b.jpg"), np.zeros((10, 10, 3), dtype=np.uint8))
        (imagens / "ignorar.txt").write_text("x", encoding="utf-8")

        predicoes = predicoes_de_detector(_DetectorFalso(), imagens)
        assert [p.imagem for p in predicoes] == ["a", "b"]
        assert all(p.confianca == pytest.approx(0.9) for p in predicoes)

    def test_filtra_classes(self, tmp_path: Path):
        imagens = tmp_path / "images"
        imagens.mkdir()
        cv2.imwrite(str(imagens / "a.jpg"), np.zeros((10, 10, 3), dtype=np.uint8))
        assert predicoes_de_detector(_DetectorFalso(class_id=1), imagens, classes=(0,)) == []
        assert len(predicoes_de_detector(_DetectorFalso(class_id=1), imagens, classes=(1,))) == 1

    def test_pasta_ausente(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            predicoes_de_detector(_DetectorFalso(), tmp_path / "nao_existe")
