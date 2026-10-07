"""US07 · Detecção de objetos e áreas críticas com YOLOv8.

Fluxo: vídeo → OpenCV → YOLOv8 → detecções → ROI poligonal → estado →
``entrada_area_critica``/``saida_area_critica`` no contrato da US04.

Uso mínimo::

    from contratos import EmissorJsonl
    from video import Configuracao, PipelineAreaCritica

    config = Configuracao.de_arquivo("config/exemplo_us07.json")
    pipeline = PipelineAreaCritica(config, emissor=EmissorJsonl("saida/eventos.jsonl"))
    resultado = pipeline.executar()

Cada camada é importável isoladamente (:mod:`video.geometria`,
:mod:`video.areas`, :mod:`video.monitor`, :mod:`video.detector`), o que mantém a
lógica testável sem pesos baixados e sem GPU.
"""

from .areas import (
    AREA_MINIMA_PADRAO,
    AreaCritica,
    AvaliacaoContencao,
    ConjuntoAreas,
    areas_de_dados,
    carregar_areas,
)
from .config import (
    SEVERIDADE_PADRAO,
    VERSAO_MODULO,
    Configuracao,
    ConfiguracaoDetector,
    ConfiguracaoEvento,
    ConfiguracaoFonte,
    ConfiguracaoMonitor,
    ConfiguracaoVisualizacao,
    ErroDeConfiguracao,
)
from .detector import (
    CLASSES_EXIGEM_FINETUNING,
    CLASSES_INTERESSE_COCO,
    CONFIDENCIA_PADRAO,
    IOU_NMS_PADRAO,
    MODELOS_YOLO_V8,
    Detector,
    DetectorFalso,
    DetectorYolo,
    ErroDeClasse,
    ErroDeDetector,
)
from .eventos import ContextoTraducao, TradutorEventos
from .geometria import (
    PoligonoInvalido,
    area_interseccao_caixa_poligono,
    fracao_da_caixa_dentro,
    iou,
    ponto_esta_dentro,
    validar_poligono,
)
from .leitor_video import (
    ErroDeVideo,
    LeitorVideo,
    VideoNaoAberto,
    VideoNaoEncontrado,
    VideoSemQuadros,
    criar_video_teste,
)
from .metricas import MetricasDeteccao, avaliar_detector, avaliar_pesos
from .modelos import Caixa, Deteccao, MetadadosVideo, Poligono, Ponto, Quadro
from .monitor import (
    EventoAreaCritica,
    MonitorAreas,
    Transicao,
    transicoes_de,
)
from .pipeline import PipelineAreaCritica, ResultadoPipeline, ResumoExecucao

__all__ = [
    "AREA_MINIMA_PADRAO",
    "CLASSES_EXIGEM_FINETUNING",
    "CLASSES_INTERESSE_COCO",
    "CONFIDENCIA_PADRAO",
    "IOU_NMS_PADRAO",
    "MODELOS_YOLO_V8",
    "SEVERIDADE_PADRAO",
    "VERSAO_MODULO",
    "AreaCritica",
    "AvaliacaoContencao",
    "Caixa",
    "Configuracao",
    "ConfiguracaoDetector",
    "ConfiguracaoEvento",
    "ConfiguracaoFonte",
    "ConfiguracaoMonitor",
    "ConfiguracaoVisualizacao",
    "ConjuntoAreas",
    "ContextoTraducao",
    "Deteccao",
    "Detector",
    "DetectorFalso",
    "DetectorYolo",
    "ErroDeClasse",
    "ErroDeConfiguracao",
    "ErroDeDetector",
    "ErroDeVideo",
    "EventoAreaCritica",
    "LeitorVideo",
    "MetadadosVideo",
    "MetricasDeteccao",
    "MonitorAreas",
    "PipelineAreaCritica",
    "Poligono",
    "PoligonoInvalido",
    "Ponto",
    "Quadro",
    "ResultadoPipeline",
    "ResumoExecucao",
    "TradutorEventos",
    "Transicao",
    "VideoNaoAberto",
    "VideoNaoEncontrado",
    "VideoSemQuadros",
    "area_interseccao_caixa_poligono",
    "areas_de_dados",
    "avaliar_detector",
    "avaliar_pesos",
    "carregar_areas",
    "criar_video_teste",
    "fracao_da_caixa_dentro",
    "iou",
    "ponto_esta_dentro",
    "transicoes_de",
    "validar_poligono",
]
