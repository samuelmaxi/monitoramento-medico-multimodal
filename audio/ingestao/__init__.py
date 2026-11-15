"""Ingestão e pré-processamento de áudios de consultas (US10).

Entrada: arquivos de áudio de qualquer formato comum. Saída, por áudio, em
``saida/audio/<audio_id>/``:

- ``convertido.wav``: WAV PCM 16 kHz, 16 bits, mono (formato recomendado pelo Azure Speech);
- ``features.wav``: cópia normalizada e sem silêncios longos, usada só na extração de features;
- ``metadados.json``: duração, taxa de amostragem, canais, origem e licença.

O arquivo original nunca é alterado.
"""

__version__ = "1.0.0"
