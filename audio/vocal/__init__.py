"""Biomarcadores vocais (US12) e classificação de alterações vocais em achados de áudio (US14).

Fluxo, por áudio já ingerido (``audio.ingestao``):

1. segmentos de fala do paciente (transcrição do Azure Speech da US11, ou VAD por energia);
2. Praat/parselmouth: F0, jitter, shimmer, HNR e intensidade por segmento; pausas e taxa de fala;
3. YAMNet (ONNX): eventos respiratórios (tosse, respiração, sibilo, arfar) com score;
4. comparação com a baseline do próprio paciente;
5. regras documentadas: ``fadiga_vocal``, ``dificuldade_respiratoria``, ``possivel_disartria``
   ou ``sem_achados``, sempre com as features que levaram à decisão;
6. ``EventoAchado`` (modalidade ``audio``) no barramento; severidade alta vira alerta (US19).

Os achados são **indicativos para avaliação clínica, não diagnóstico**.
"""

__version__ = "0.1.0"
MODEL_VERSION = f"audio-achados@{__version__}"
