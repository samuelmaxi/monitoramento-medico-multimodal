"""US16 · Anomalias na evolução de prescrições (alterações inesperadas no tratamento).

Fluxo: ``mimic.carregar`` (tabelas ``prescriptions`` + ``pharmacy`` do MIMIC-IV
Demo) → ``normalizacao`` (unidade, dose, via, frequência) → ``principios``
(princípio ativo) → ``regras`` (quatro detectores) → ``eventos`` (contrato da
US04). Limiares e a lista ISMP ficam em ``config/us16_prescricoes.yaml``.
"""

__version__ = "0.1.0"
MODEL_VERSION = f"anomalias-prescricoes@{__version__}"
