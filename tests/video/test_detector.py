"""Parsing e configuração do adaptador Ultralytics, sem carregar pesos."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from video import Caixa, ConfiguracaoDetector, DetectorYolo, ErroDeClasse, ErroDeDetector

NOMES = {0: "person", 1: "bed", 2: "chair"}


class ModeloFalso:
    names = NOMES

    def __init__(self, resultado):
        self.resultado = resultado
        self.argumentos = None

    def predict(self, imagem, **argumentos):
        self.argumentos = argumentos
        return [self.resultado]

    def track(self, imagem, **argumentos):
        self.argumentos = argumentos
        return [self.resultado]


def resultado(boxes=None):
    return SimpleNamespace(boxes=boxes)


def caixas(xyxy, conf, cls, ids=None):
    return SimpleNamespace(xyxy=xyxy, conf=conf, cls=cls, id=ids)


def test_interpretar_converte_boxes_ids_e_timestamp():
    modelo = ModeloFalso(resultado(caixas([[1, 2, 30, 40]], [0.8], [0], [27])))
    detector = DetectorYolo(ConfiguracaoDetector(classes=("person",)), modelo=modelo)
    deteccoes = detector.interpretar(modelo.resultado, indice_quadro=12, tempo_s=1.2)
    assert len(deteccoes) == 1
    assert deteccoes[0].caixa == Caixa(1.0, 2.0, 30.0, 40.0)
    assert deteccoes[0].class_name == "person"
    assert deteccoes[0].track_id == 27
    assert deteccoes[0].timestamp_video_s == 1.2


def test_interpretar_filtra_classes_nao_configuradas():
    saida = resultado(caixas([[1, 2, 30, 40]], [0.8], [2]))
    detector = DetectorYolo(ConfiguracaoDetector(classes=("person",)), modelo=ModeloFalso(saida))
    assert detector.interpretar(saida, indice_quadro=0, tempo_s=0.0) == []


def test_interpretar_sem_boxes_retorna_lista_vazia():
    detector = DetectorYolo(ConfiguracaoDetector(classes=("person",)), modelo=ModeloFalso(resultado()))
    assert detector.interpretar(resultado(), indice_quadro=0, tempo_s=0.0) == []


def test_interpretar_rejeita_saida_inconsistente():
    saida = resultado(caixas([[1, 2, 3, 4]], [0.8, 0.9], [0]))
    detector = DetectorYolo(ConfiguracaoDetector(classes=("person",)), modelo=ModeloFalso(saida))
    with pytest.raises(ErroDeDetector, match="inconsistente"):
        detector.interpretar(saida, indice_quadro=0, tempo_s=0.0)


def test_classe_desconhecida_para_o_peso_e_recusada():
    with pytest.raises(ErroDeClasse, match="fine-tuning"):
        DetectorYolo(
            ConfiguracaoDetector(classes=("instrumento_cirurgico",)),
            modelo=ModeloFalso(resultado()),
        )


def test_reiniciar_rastreamento_descarta_trackers_do_predictor():
    predictor = SimpleNamespace(trackers=[object()])
    modelo = ModeloFalso(resultado())
    modelo.predictor = predictor
    detector = DetectorYolo(ConfiguracaoDetector(classes=("person",)), modelo=modelo)
    detector.reiniciar_rastreamento()
    assert not hasattr(predictor, "trackers")


def test_reiniciar_rastreamento_e_seguro_sem_predictor():
    modelo = ModeloFalso(resultado())
    detector = DetectorYolo(ConfiguracaoDetector(classes=("person",)), modelo=modelo)
    detector.reiniciar_rastreamento()  # não deve levantar


def test_detectar_usa_limites_configurados_e_tracking_persistente():
    saida = resultado(caixas([[1, 2, 30, 40]], [0.8], [0], [2]))
    modelo = ModeloFalso(saida)
    config = ConfiguracaoDetector(
        classes=("person",), confianca=0.25, iou=0.5, imgsz=320, dispositivo="cpu"
    )
    detector = DetectorYolo(config, modelo=modelo)
    detector.detectar(object(), indice_quadro=0, tempo_s=0.0)
    assert modelo.argumentos["conf"] == 0.25
    assert modelo.argumentos["iou"] == 0.5
    assert modelo.argumentos["imgsz"] == 320
    assert modelo.argumentos["classes"] == [0]
    assert modelo.argumentos["persist"] is True
    assert "half" not in modelo.argumentos
    assert "quantize" not in modelo.argumentos


def test_detectar_usa_quantize_16_somente_em_gpu():
    for dispositivo in ("cuda", "cuda:0", "0", "CUDA:1"):
        config = ConfiguracaoDetector(classes=("person",), dispositivo=dispositivo)
        modelo = ModeloFalso(resultado())
        detector = DetectorYolo(config, modelo=modelo)
        detector.detectar(object(), indice_quadro=0, tempo_s=0.0)
        assert modelo.argumentos["quantize"] == 16
        assert "half" not in modelo.argumentos


def test_detectar_nao_usa_fp16_sem_gpu():
    for dispositivo in (None, "cpu", "mps"):
        config = ConfiguracaoDetector(classes=("person",), dispositivo=dispositivo)
        modelo = ModeloFalso(resultado())
        detector = DetectorYolo(config, modelo=modelo)
        detector.detectar(object(), indice_quadro=0, tempo_s=0.0)
        assert "quantize" not in modelo.argumentos
        assert "half" not in modelo.argumentos
