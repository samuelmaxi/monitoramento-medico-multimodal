"""US09 · Relatório automático de desvios no procedimento.

Ao final da análise de cada vídeo, junta o que a US06 (OpenPose), a US07 (YOLOv8) e a US08
(desvios do padrão esperado) produziram em um relatório que a equipe lê sem editar nada:

- ``<fonte>_relatorio_desvios.json``: o relatório completo, para máquinas;
- ``<fonte>_relatorio_desvios.html``: o mesmo conteúdo em um arquivo único, para pessoas
  (imprime em PDF pelo navegador).

    python -m video.relatorio.run --perfil internacao_leito \\
        --pose outputs/pose/<video>.json --eventos-areas saida/video/eventos_<video>.jsonl

Camadas: :mod:`.config` (critérios de apresentação), :mod:`.tempo` (% do tempo em desvio),
:mod:`.confianca` (confiança de cada desvio), :mod:`.privacidade` (nada de dado pessoal),
:mod:`.construcao` (o relatório), :mod:`.html` (a página) e :mod:`.run` (CLI e alertas).
"""

__version__ = "0.1.0"

MODEL_VERSION = f"video-relatorio@{__version__}"
SCHEMA_VERSION = "1.0"
