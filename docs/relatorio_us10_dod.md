# US10 · Ingestão e pré-processamento de áudios de consultas: rastreabilidade ao DoD

Pacote [`audio/ingestao/`](../audio/ingestao/). Desenho em
[`arquitetura/us12_biomarcadores_vocais.md`](arquitetura/us12_biomarcadores_vocais.md).

## Como reproduzir

```bash
# converte uma pasta de áudios; o erro de um arquivo não interrompe os demais
uv run python -m audio.ingestao.run --input gravacoes/ --origem "consulta simulada" --licenca "CC0"

uv run pytest tests/audio/test_ingestao_conversao.py tests/audio/test_ingestao_validacao.py \
  tests/audio/test_ingestao_preparo.py tests/audio/test_ingestao_lote.py \
  tests/audio/test_ingestao_metadados.py
```

Por áudio, em `saida/audio/<audio_id>/`: `convertido.wav`, `features.wav` e `metadados.json`; o lote
resume em `saida/audio/metadados.csv`. Limiares e formatos em
[`audio/ingestao/config.yaml`](../audio/ingestao/config.yaml).

## Itens do DoD

| # | Item | Situação | Evidência |
| --- | --- | --- | --- |
| 1 | Entradas convertidas para WAV PCM 16 kHz, 16 bits, mono | Atendido | `conversao.converter` grava e **relê** o arquivo; `validar_wav` confere formato, codificação, taxa, canais e duração. Testado de 8 a 48 kHz, mono e estéreo, e com OGG/Vorbis e OGG/Opus (o formato dos áudios do WhatsApp), sem ffmpeg. `tests/audio/test_ingestao_conversao.py` |
| 2 | Áudio corrompido, vazio ou em formato não suportado: erro logado, sem interromper o lote | Atendido | Erros tipados em `erros.py` (`audio_vazio`, `audio_corrompido`, `formato_nao_suportado`, `audio_curto`, `audio_longo`, `audio_id_invalido`). `ingerir_lote` registra cada falha em log e em `metadados.csv` e segue; um bug inesperado em um arquivo também não derruba o lote. A CLI sai com 2 se algum falhou. `test_ingestao_validacao.py`, `test_ingestao_lote.py` |
| 3 | Normalização e remoção de silêncios longos só na cópia de features; original preservado | Atendido | `preparo.py` altera apenas `features.wav`. Os testes provam que o sha256 do original não muda, que o `convertido.wav` mantém as pausas e o volume e que só a cópia perde o silêncio longo. A remoção encurta cada silêncio de pelo menos 1 s para 0,3 s (não só as pontas). `test_ingestao_preparo.py` |
| 4 | Metadados: duração, taxa de amostragem, canais, origem/licença | Atendido | `metadados.py`: duração, taxa e canais do original e do convertido, formato e codec de origem, sha256, `origem` e `licenca` (`--origem`/`--licenca`; o padrão pede confirmar o consentimento), status e erro. Caminhos relativos, e o arquivo de origem só pelo nome. `test_ingestao_metadados.py` |
| 5 | Nenhum áudio com dado pessoal identificável versionado | Atendido | `.gitignore` cobre `tratamento_audio/` e `*.wav *.ogg *.oga *.opus *.m4a *.mp3 *.flac *.aac *.wma *.amr`; os testes geram áudio sintético em `tmp_path`. `test_nenhum_audio_esta_versionado_no_repositorio` roda `git ls-files` e falha se achar um |
| 6 | Testes unitários da conversão e validação de formato | Atendido | `tests/audio/test_ingestao_*.py` |

## Decisões e ressalvas

- **Decodificação.** `soundfile` (libsndfile 1.2) lê WAV, FLAC e OGG/Opus sem ffmpeg. MP3, M4A, AAC,
  WMA e AMR precisam do ffmpeg no PATH (`winget install Gyan.FFmpeg`); sem ele o erro diz isso. Pelo
  ffmpeg a taxa e os canais originais se perdem (o ffmpeg converte direto), então esses campos ficam
  vazios e os `convertido_*` valem.
- **`app/audio_convert.py`** ganhou `timeout_s` (padrão 300 s): um arquivo malformado não trava o lote.
- **`audio_id` vira nome de pasta** e é validado (letra ou número no início): o padrão do contrato
  aceita `..`, que escreveria fora da pasta de saída.
- **A conversão não é lossless para o conteúdo:** reamostrar e fazer downmix para mono é o que o Azure
  Speech recomenda, mas descarta o estéreo.
- O protótipo em `tratamento_audio/` (notebook) foi a base; a lógica de leitura, conversão e cópia de
  features foi reescrita com configuração em YAML, erros tipados e testes. O notebook e os áudios dele
  continuam só na máquina local.
