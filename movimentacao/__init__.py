"""US17 · Anomalias nos padrões de movimentação durante a internação.

Monta, por paciente, uma série de movimentação (posição no leito, saídas do
leito, quedas, período dia/noite) a partir de fontes que outras US já gravam:

- vídeo: pose da US06, áreas críticas da US07 e quedas da US08
  (:mod:`.video`);
- registro de enfermagem do MIMIC-IV Demo (``Position``/``Turn``/``Activity``
  do ``chartevents``), que cobre dias de internação (:mod:`.mimic`).

Sobre a série, calcula métricas e aplica os limiares de
``config/us17_movimentacao.yaml``: imobilidade > 2 h (NPIAP/EPUAP), saída do
leito (alta à noite), queda (sempre alta) e desvio da baseline do próprio
paciente. Cada achado vira um ``EventoAchado`` da US04, modalidade
``movimentacao``.

    python -m movimentacao.run mimic --stay-id 32604416

Camadas: :mod:`.config` (limiares), :mod:`.serie` (marcos e métricas),
:mod:`.regras` (detectores), :mod:`.eventos` (contrato da US04),
:mod:`.injecao` e :mod:`.avaliacao` (casos rotulados e recall).
"""

__version__ = "0.1.0"

MODEL_VERSION = f"anomalias-movimentacao@{__version__}"
