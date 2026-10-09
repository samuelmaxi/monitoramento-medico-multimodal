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

from .anotacoes import (
    Anotacao,
    ErroAnotacao,
    ProblemaAnotacao,
    escrever_label,
    exportar_para_anotacao,
    importar_anotacoes,
    ler_label,
    manifesto_anotacoes,
    validar_arquivo_label,
)
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
from .dataset import (
    CLASSES_PADRAO,
    PROPORCOES_PADRAO,
    SPLITS_PADRAO,
    AvaliacaoAptidao,
    GrupoVideo,
    RegistroFrame,
    ResultadoGate,
    agrupar_por_origem,
    avaliar_gate,
    criar_estrutura,
    gerar_data_yaml,
    materializar_divisao,
    planejar_divisao,
    verificar_aptidao,
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
from .extracao_frames import (
    ConfiguracaoExtracao,
    FrameExtraido,
    ManifestoFrames,
    RelatorioExtracao,
    extrair_frames,
    extrair_lote,
)
from .geometria import (
    PoligonoInvalido,
    area_interseccao_caixa_poligono,
    fracao_da_caixa_dentro,
    iou,
    ponto_esta_dentro,
    validar_poligono,
)
from .inventario import (
    InventarioVideo,
    inventariar,
    inventariar_video,
    resumo_inventario,
    salvar_inventario,
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
from .nomes_video import NomeVideo, parsear_nome
from .pipeline import PipelineAreaCritica, ResultadoPipeline, ResumoExecucao
from .validacao_dataset import RelatorioValidacao, salvar_relatorio, validar_dataset

__all__ = [
    "AREA_MINIMA_PADRAO",
    "CLASSES_EXIGEM_FINETUNING",
    "CLASSES_INTERESSE_COCO",
    "CLASSES_PADRAO",
    "CONFIDENCIA_PADRAO",
    "IOU_NMS_PADRAO",
    "MODELOS_YOLO_V8",
    "PROPORCOES_PADRAO",
    "SEVERIDADE_PADRAO",
    "SPLITS_PADRAO",
    "VERSAO_MODULO",
    "Anotacao",
    "AreaCritica",
    "AvaliacaoAptidao",
    "AvaliacaoContencao",
    "Caixa",
    "Configuracao",
    "ConfiguracaoDetector",
    "ConfiguracaoEvento",
    "ConfiguracaoExtracao",
    "ConfiguracaoFonte",
    "ConfiguracaoMonitor",
    "ConfiguracaoVisualizacao",
    "ConjuntoAreas",
    "ContextoTraducao",
    "Deteccao",
    "Detector",
    "DetectorFalso",
    "DetectorYolo",
    "ErroAnotacao",
    "ErroDeClasse",
    "ErroDeConfiguracao",
    "ErroDeDetector",
    "ErroDeVideo",
    "EventoAreaCritica",
    "FrameExtraido",
    "GrupoVideo",
    "InventarioVideo",
    "LeitorVideo",
    "ManifestoFrames",
    "MetadadosVideo",
    "MetricasDeteccao",
    "MonitorAreas",
    "NomeVideo",
    "PipelineAreaCritica",
    "Poligono",
    "PoligonoInvalido",
    "Ponto",
    "ProblemaAnotacao",
    "Quadro",
    "RegistroFrame",
    "RelatorioExtracao",
    "RelatorioValidacao",
    "ResultadoGate",
    "ResultadoPipeline",
    "ResumoExecucao",
    "TradutorEventos",
    "Transicao",
    "VideoNaoAberto",
    "VideoNaoEncontrado",
    "VideoSemQuadros",
    "agrupar_por_origem",
    "area_interseccao_caixa_poligono",
    "areas_de_dados",
    "avaliar_detector",
    "avaliar_gate",
    "avaliar_pesos",
    "carregar_areas",
    "criar_estrutura",
    "criar_video_teste",
    "escrever_label",
    "exportar_para_anotacao",
    "extrair_frames",
    "extrair_lote",
    "fracao_da_caixa_dentro",
    "gerar_data_yaml",
    "importar_anotacoes",
    "inventariar",
    "inventariar_video",
    "iou",
    "ler_label",
    "manifesto_anotacoes",
    "materializar_divisao",
    "parsear_nome",
    "planejar_divisao",
    "ponto_esta_dentro",
    "resumo_inventario",
    "salvar_inventario",
    "salvar_relatorio",
    "transicoes_de",
    "validar_arquivo_label",
    "validar_dataset",
    "validar_poligono",
    "verificar_aptidao",
]
