"""Motor de alertas automáticos para a equipe médica (US19).

Assina o ``BarramentoLocal`` e não conhece modalidade alguma: qualquer ``EventoAchado``
vira alerta conforme ``config/alertas_regras.yaml``.
"""

from .config import ConfigAlertas
from .despachante import Despachante
from .fabrica import construir
from .modelos import Alerta, EventoDeAlerta, StatusAlerta, TipoAuditoria
from .motor import MotorAlertas
from .regras import avaliar, carregar_regras
from .servico import ServicoAlertas
from .simulador import cenario_news2_emergencia, evento_sintetico, todos_os_tipos

__all__ = [
    "Alerta", "ConfigAlertas", "Despachante", "EventoDeAlerta", "MotorAlertas", "ServicoAlertas",
    "StatusAlerta", "TipoAuditoria", "avaliar", "carregar_regras", "cenario_news2_emergencia",
    "construir", "evento_sintetico", "todos_os_tipos",
]
