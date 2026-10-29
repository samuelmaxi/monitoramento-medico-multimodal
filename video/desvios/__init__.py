"""US08 · Movimentos e eventos fora do padrão esperado no vídeo.

Combina a postura da US06 (OpenPose: keypoints e ângulos por quadro) com os
objetos/áreas da US07 (YOLOv8: entrada/saída de área crítica) e compara com o
padrão esperado do tipo de vídeo, definido em ``config/us08_padroes.yaml``.
Cada desvio que persiste pela janela mínima vira um ``EventoAchado`` da US04.

    python -m video.desvios.run --perfil fisioterapia_abducao_ombro_frontal \\
        --pose outputs/pose/PM_001-Camera17-30fps.json --patient-id 1

Camadas: :mod:`.config` (perfis e limiares), :mod:`.sinais` (trechos
persistentes), :mod:`.regras` e :mod:`.areas` (desvios), :mod:`.eventos`
(contrato da US04), :mod:`.evidencia` (quadro anotado), :mod:`.avaliacao`
(precisão e recall contra vídeos rotulados).
"""

__version__ = "0.1.0"

MODEL_VERSION = f"video-desvios@{__version__}"
