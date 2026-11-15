"""US18 · Fusão dos achados multimodais em score de risco por paciente.

Uso típico::

    from contratos import BarramentoLocal
    from fusao import FusaoMultimodal, carregar_config

    barramento = BarramentoLocal("saida/eventos.jsonl")
    FusaoMultimodal(carregar_config()).assinar(barramento)   # antes do motor de alertas
    # detectores publicam no barramento; a fusão publica risco_multimodal nele

Ou, sem barramento (relatório, API, testes)::

    from fusao import alinhar, calcular_risco
    riscos = {p: calcular_risco(a, config) for p, a in alinhar(eventos, config.janela).items()}
"""

__version__ = "0.1.0"
MODEL_VERSION = f"fusao-late-rules@{__version__}"

from .alinhamento import Alinhamento, alinhar, alinhar_paciente, na_janela  # noqa: E402
from .config import CAMINHO_PADRAO, ConfigFusao, ErroDeConfiguracao, de_dict  # noqa: E402
from .config import carregar as carregar_config  # noqa: E402
from .eventos import RISCO_MULTIMODAL, evento_de_risco  # noqa: E402
from .score import Concordancia, Risco, SinalModalidade, calcular_risco  # noqa: E402
from .servico import FusaoMultimodal  # noqa: E402

__all__ = [
    "CAMINHO_PADRAO", "MODEL_VERSION", "RISCO_MULTIMODAL", "Alinhamento", "Concordancia",
    "ConfigFusao", "ErroDeConfiguracao", "FusaoMultimodal", "Risco", "SinalModalidade",
    "alinhar", "alinhar_paciente", "calcular_risco", "carregar_config", "de_dict",
    "evento_de_risco", "na_janela",
]
