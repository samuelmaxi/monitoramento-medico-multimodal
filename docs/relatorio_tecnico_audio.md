# Relatório técnico: áudio vocal no monitoramento médico multimodal

**Tech Challenge, Fase 4 (US10, US12 e US14).** Este relatório consolida a arquitetura, os modelos,
os resultados e as limitações do módulo de áudio vocal: ingestão e pré-processamento (US10),
biomarcadores vocais (US12) e classificação e geração de achados (US14). A transcrição (US11) e a
análise de texto (US13) estão no [relatório técnico geral](relatorio_tecnico.md) e aparecem aqui só
onde se encontram com este módulo. Os detalhes de cada história ficam nos relatórios de
rastreabilidade ao DoD em [`docs/`](.), citados em cada seção. Os caminhos são relativos à raiz do
repositório.

> **Aviso.** Este módulo **não é um dispositivo médico** e **não foi validado clinicamente**. É um
> trabalho acadêmico. Toda saída é um *indicativo para avaliação clínica, não um diagnóstico*, e
> nenhuma substitui julgamento profissional. Os limiares marcados como experimentais (`[EXP]`) e as
> prioridades de alerta são provisórios e precisam de revisão por fonoaudiólogos e médicos antes de
> qualquer uso real.

## Sumário

1. [Escopo](#1-escopo)
2. [Fluxo do áudio](#2-fluxo-do-áudio)
3. [Modelos e métodos](#3-modelos-e-métodos)
4. [Resultados](#4-resultados)
5. [Exemplos de achados](#5-exemplos-de-achados)
6. [Dados, licenças e LGPD](#6-dados-licenças-e-lgpd)
7. [Limitações e trabalhos futuros](#7-limitações-e-trabalhos-futuros)
8. [Referências](#8-referências)

## 1. Escopo

O módulo recebe a gravação da voz de um paciente (um áudio de voz do WhatsApp, ou uma consulta com
médico e paciente), prepara o sinal, mede características da voz e publica achados no mesmo contrato
`EventoAchado` dos demais detectores, de onde o motor de alertas (US19) os consome.

| Etapa | O que faz | US |
| --- | --- | --- |
| Ingestão e conversão | Lê WAV, FLAC, OGG/Opus, MP3, M4A, AAC, WMA, AMR e WebM. Grava WAV PCM 16 bits, 16 kHz, mono, relê e confere o arquivo | US10 |
| Pré-processamento | Cópia com volume normalizado e silêncios longos removidos, usada só nas features. O original nunca é alterado | US10 |
| Metadados e lote | Duração, taxa, canais, origem, licença, sha256 e status por áudio. Um arquivo ruim é registrado e o lote continua | US10 |
| Biomarcadores vocais | F0 (média e variação), jitter local, shimmer local, HNR, intensidade, pausas, taxa de fala e de articulação, com Praat | US12 |
| Eventos respiratórios | Tosse, respiração, sibilo, arfar e respiração ofegante, com YAMNet (AudioSet) e score por trecho | US12 |
| Baseline do paciente | Compara o áudio com os anteriores do mesmo paciente e emite `desvio_baseline_vocal` | US12 |
| Classificação e achados | `fadiga_vocal`, `dificuldade_respiratoria`, `possivel_disartria` ou normal (`sem_achados`), cada um com os critérios que o explicam | US14 |
| Interfaces | CLI (`audio.ingestao.run`, `audio.vocal.run`), rota `POST /audio/vocal` e script de avaliação | US10, US12, US14 |

**Fora deste relatório:** transcrição com Azure Speech (US11), termos críticos e sentimento no texto
(US13) e a fusão multimodal (US18, não implementada). A transcrição entra aqui como *entrada
opcional*: ela diz quais trechos são do paciente e quantas palavras ele falou.

## 2. Fluxo do áudio

```
áudio (qualquer formato aceito)
  │  audio.ingestao
  ├─ lê: soundfile/libsndfile (WAV, FLAC, OGG/Opus) ou, se falhar, ffmpeg (MP3, M4A, AAC, WMA, AMR, WebM)
  ├─ convertido.wav ........ WAV PCM 16 bits, 16 kHz, mono (é o que vai ao Azure Speech)
  ├─ features.wav .......... cópia normalizada, sem silêncios longos (só para as features)
  └─ metadados.json ........ duração, taxa, canais, origem, licença, sha256, status
  │  audio.vocal
  ├─ segmentos do paciente: JSON do Azure Speech (US11) ou, sem ele, VAD por energia
  ├─ Praat/parselmouth: F0, jitter, shimmer, HNR, intensidade, pausas, taxa de fala
  ├─ YAMNet (ONNX): tosse, respiração, sibilo, arfar, com score por trecho
  ├─ baseline do próprio paciente (mediana/MAD dos áudios anteriores)
  ├─ regras: fadiga_vocal | dificuldade_respiratoria | possivel_disartria | normal
  └─ EventoAchado (modalidade audio) → barramento → motor de alertas (US19)
```

O fluxo tem sete passos:

1. **Entrada.** Um arquivo ou uma pasta de áudios, pela CLI ou por `POST /audio/vocal`. O
   `patient_id` é pseudonimizado antes de qualquer cálculo; sem a `PSEUDONYM_KEY` a execução falha
   cedo, antes de converter o áudio.
2. **Ingestão (US10).** Decodifica, valida, converte para WAV PCM 16 kHz mono e grava os metadados.
3. **Pré-processamento (US10).** Gera `features.wav` (normalizado, sem silêncios longos) e o mapa que
   leva cada instante do original ao instante equivalente da cópia.
4. **Segmentação.** Define os trechos de fala do paciente (transcrição ou VAD).
5. **Medição (US12).** Biomarcadores por segmento e eventos respiratórios no áudio todo.
6. **Classificação (US14).** Compara com a baseline do paciente, aplica as regras e escolhe a classe.
7. **Publicação.** Monta os `EventoAchado`, grava `eventos.jsonl` e `relatorio.json` e publica no
   barramento compartilhado. Severidade `alta` vira alerta sem mudar o motor.

Saídas por áudio, em `saida/audio/<audio_id>/`: `convertido.wav`, `features.wav`, `metadados.json`,
`eventos.jsonl`, `relatorio.json` e, com `--transcrever`, `convertido.json` (a transcrição, com o
paciente já pseudonimizado). O lote resume em `saida/audio/metadados.csv`, e a baseline de cada
paciente fica em `saida/audio/baseline/<pt_...>.json`.

### Qual sinal alimenta cada medida

A US10 manda normalizar o volume e remover silêncios longos **só na cópia usada para as features**.
Isso decide o desenho: a remoção de silêncio apagaria justamente as pausas, e a normalização apagaria a
diferença entre gravações altas e baixas. Por isso cada medida usa o sinal que não a distorce.

| Medida | Sinal | Por quê |
| --- | --- | --- |
| F0, jitter, shimmer, HNR | `features.wav` (recalculada em memória) | Não dependem do volume nem das pausas; a normalização ajuda o detector de F0 |
| Proporção e duração das pausas, taxa de fala e de articulação | `convertido.wav` | A cópia perderia as pausas longas |
| Intensidade | `convertido.wav` | A normalização apagaria o nível real da gravação |
| Eventos respiratórios (YAMNet) | `convertido.wav` | Decisão de projeto: o ganho de até 5× da normalização amplificaria o ruído de fundo, e respiração é um som fraco que se confunde com ele |

Os segmentos da transcrição estão no tempo do `convertido.wav`. `CopiaFeatures.indice`
(`audio/ingestao/preparo.py`) mapeia cada amostra do original para a cópia, e `recortar(inicio_s, fim_s)`
devolve o trecho equivalente.

## 3. Modelos e métodos

### 3.1 Ingestão e conversão de formato (US10)

Pacote `audio/ingestao/`, parâmetros em [`audio/ingestao/config.yaml`](../audio/ingestao/config.yaml)
(versão **1.0.0**). Detalhes no [relatório US10](relatorio_us10_dod.md).

| Item | Valor | Justificativa |
| --- | --- | --- |
| Formato de saída | WAV, PCM 16 bits, mono, **16 kHz** | Formato recomendado pelo Azure Speech. Profundidade e canais são fixos no código; a taxa vem do YAML |
| Decodificação | `soundfile` (libsndfile) tentado primeiro; `ffmpeg` como plano B | Só WAV, FLAC e OGG/Opus (o formato dos áudios do WhatsApp) estão garantidos sem ffmpeg, o que permite testar sem ele. MP3, M4A, AAC, WMA, AMR e WebM dependem do ffmpeg |
| Reamostragem | `scipy.signal.resample_poly` (polifásica, razão reduzida pelo MDC das taxas) | Filtro anti-aliasing embutido; razão racional exata, como 160/441 para 44,1 → 16 kHz |
| Mono | média dos canais | O que o Azure Speech recomenda, ao custo de descartar o estéreo |
| Proteção contra estouro | se o pico passa de 1,0 após o downmix, o sinal é reduzido, não cortado | Evita distorção por *clipping* |
| Conferência | o WAV gravado é **relido** (`sf.info`) e validado: formato, codificação, taxa, canais e duração | Um arquivo fora do formato é apagado e vira erro, nunca segue adiante |
| Timeout do ffmpeg | 300 s (`app/audio_convert.py`, parâmetro `timeout_s`) | Um arquivo malformado não trava o lote |

**Validação de entrada.** Cada falha tem um erro tipado com código estável, usado em log, metadados e
API:

| Código | Quando |
| --- | --- |
| `arquivo_nao_encontrado` | caminho inexistente ou que não é arquivo |
| `formato_nao_suportado` | extensão fora de `wav, flac, ogg, oga, opus, mp3, m4a, aac, wma, amr, webm`, ou formato que exige ffmpeg ausente do PATH |
| `audio_vazio` | 0 bytes, sem amostras ou silêncio digital (pico < 0,0001, ≈ −80 dBFS) |
| `audio_corrompido` | não decodifica, taxa inválida ou amostras NaN/Inf |
| `audio_curto` | duração < 0,5 s (não há fala para transcrever nem para medir jitter e shimmer) |
| `audio_longo` | duração > 3600 s (a decodificação carrega o áudio inteiro como `float32`); conferido antes de decodificar |
| `audio_id_invalido` | `audio_id` que não serve de nome de pasta (ver seção 6) |

**Lote.** `ingerir_lote` registra cada falha em log e em `metadados.csv` (`status=erro`, com o código) e
segue para o próximo arquivo. Um erro inesperado em um arquivo também não derruba o lote. A CLI sai
com `0` (todos ingeridos), `1` (nada a processar ou configuração inválida) ou `2` (algum falhou).

**Metadados por áudio** (`metadados.json` e `metadados.csv`): `audio_id`, nome do arquivo (relativo à
pasta de entrada, nunca o caminho absoluto), status e erro, sha256 e tamanho, formato, codec e
decodificador de origem, duração, taxa e canais **do original**, `origem` e `licenca`, os mesmos dados
do WAV convertido, da cópia de features (duração, silêncio removido, ganho aplicado),
`original_preservado` (sha256 antes e depois), versão da configuração e instante do processamento.
Pelo ffmpeg a taxa e os canais originais se perdem (o ffmpeg converte direto), e esses campos ficam
vazios.

### 3.2 Pré-processamento e tratamento de ruído

A cópia de features (`audio/ingestao/preparo.py`) faz duas coisas, nesta ordem, e o `convertido.wav` e o
original **não sofrem nenhuma delas**:

| Etapa | Método | Parâmetros |
| --- | --- | --- |
| Normalização de volume | o pico vai ao alvo, com ganho limitado | pico alvo **0,95**; ganho máximo **5×** (≈ 14 dB) |
| Remoção de silêncios longos | quadro é silêncio se o RMS fica abaixo do nível de fala da gravação; cada silêncio longo é encurtado | janela 25 ms; limiar **−30 dB** relativo ao percentil 95 dos RMS; só silêncios ≥ **1,0 s**; permanece **0,3 s** (metade de cada lado) |

O limiar é **relativo ao nível de fala da própria gravação**, para valer em gravações altas e baixas.
Nas pontas do arquivo fica só a metade vizinha à fala; no meio, metade de cada lado, o que preserva
ataque e decaimento. Não se corta só o começo e o fim: cada silêncio de pelo menos 1 s é encurtado.

**Como o módulo lida com ruído.** O módulo **não aplica supressão de ruído** (filtro ou *denoising*).
O que existe é um conjunto de salvaguardas para que o ruído não seja confundido com fala:

- O **ganho máximo de 5×** impede que a normalização amplifique uma gravação que é quase só ruído.
- O limiar de silêncio é relativo, mas há um **piso absoluto** do nível de fala em **−50 dBFS**
  (`pausas.nivel_minimo_fala_db`). Sem ele, um segmento só de silêncio ou de ruído de ambiente
  viraria "a fala de si mesmo". Esse caso foi achado por um teste: um segmento de silêncio digital era
  contado como fala.
- O **HNR** (razão harmônicos-ruído) mede o ruído *na voz* e entra como critério clínico (seção 3.4).
- O **YAMNet** roda no `convertido.wav`, sem a normalização, pelo motivo da tabela da seção 2.

Gravações com muito ruído de fundo reduzem o HNR e podem ser lidas como voz alterada (ver seção 7).

### 3.3 Segmentação da fala do paciente

`audio/vocal/segmentos.py` decide *o que* medir. Há duas fontes:

- **Com a transcrição do Azure Speech (US11):** cada `fala` do paciente vira um segmento, com a
  contagem de palavras de que a taxa de fala precisa. `medico_falante` diz quem é o médico; **dois
  falantes sem essa informação são recusados**, porque errar mediria a voz do médico como a do
  paciente (mesma regra do `audio/text`, US13). A diarização é de até 2 falantes.
- **Exceção explícita:** áudio de **um só falante** (a gravação do próprio paciente, como um áudio de
  voz do WhatsApp) é tratado como paciente e marcado `papel_assumido`.
- A lacuna entre duas falas do paciente só conta como pausa dele se **não houver fala do médico no
  meio** (`contiguo_ao_anterior`); uma troca de turno não é pausa.
- **Sem transcrição:** VAD por energia (janela 20 ms; silêncios menores que 1 s ficam dentro do
  segmento) e **sem taxa de fala**. Os critérios que dependem dela ficam "não avaliáveis" e não contam.

Segmentos com menos de 0,3 s são ignorados (poucos ciclos de F0 para medir).

### 3.4 Biomarcadores vocais (US12)

Praat via `praat-parselmouth` **0.4.7** (Praat **6.1.38**), em `audio/vocal/biomarcadores.py`. Os
parâmetros estão em [`audio/vocal/config.yaml`](../audio/vocal/config.yaml) (versão **1.0.0**), cada um
marcado com a origem: `[MDVP]` (normalidade do MDVP, citada na US12), `[PRAAT]` (parâmetro padrão do
Praat) ou `[EXP]` (**experimental**, sem validação clínica). Detalhes no
[relatório US12](relatorio_us12_dod.md) e no
[desenho](arquitetura/us12_biomarcadores_vocais.md).

| Medida | Método | Parâmetros |
| --- | --- | --- |
| F0 (média e variação) | `To Pitch`; variação como desvio padrão em **semitons** em torno da média | faixa **75 a 500 Hz**, passo 10 ms `[PRAAT]` |
| Jitter local (%) | `To PointProcess (periodic, cc)` + `Get jitter (local)` | período 0,1 ms a 20 ms; razão máxima entre períodos 1,3 `[PRAAT]` |
| Shimmer local (%) | `Get shimmer (local)` sobre o mesmo `PointProcess` | razão máxima entre amplitudes 1,6 `[PRAAT]` |
| HNR (dB) | `To Harmonicity (cc)`, média | limiar de silêncio 0,1; 1,0 período por janela `[PRAAT]` |
| Intensidade (dB) | `To Intensity`, média dos quadros de fala (≥ máximo − 25 dB) | janela mínima do Praat (6,4 / piso de F0) |
| Pausas | silêncio de pelo menos **0,25 s**, a **25 dB** abaixo do nível de fala do paciente (percentil 95 dos RMS) | piso absoluto de −50 dBFS `[EXP]` |
| Taxa de fala | palavras da transcrição ÷ duração da fala, **incluindo** pausas | só com transcrição |
| Taxa de articulação | palavras ÷ (duração da fala − pausas) | só com transcrição |

**Voz sustentada mínima.** Com menos de **0,25 s** de voz sustentada em um segmento não há períodos
suficientes, e jitter, shimmer e HNR ficam **nulos**. Medidas que o Praat não consegue calcular viram
`None`, **nunca NaN** (o contrato rejeita NaN).

**Agregação por áudio.** Os tempos são somados. Jitter, shimmer e HNR são médias ponderadas pela voz
sustentada de cada segmento, a intensidade é ponderada pela duração, e o F0 junta os quadros
vozeados de todos os segmentos.

**Verificação do método.** Os testes usam voz sintética de parâmetros conhecidos
(`audio/vocal/injecao.py`: pulsos glóticos com 20 harmônicos, jitter, shimmer e SNR controlados): a F0
foi medida em **148,8 Hz** contra **148,35 Hz** esperados (0,3 %), e um jitter injetado de **2 %** foi
medido em **1,96 %**.

### 3.5 Eventos respiratórios com YAMNet (US12)

`audio/vocal/respiratorio.py`. O YAMNet é uma rede MobileNet treinada no AudioSet que devolve **521
scores** (uma classe cada) a cada **0,48 s**, para uma janela de **0,975 s**. Roda em **ONNX**
(`onnxruntime`, CPU), sem TensorFlow, o que o mantém leve e compatível com o Python do projeto. O
modelo e o mapa de classes são baixados por `scripts/baixar_modelos_audio.py` para `models/audio/`
(fora do Git), com o **SHA-256 fixado**: a URL aponta para uma ramificação mutável, então é o hash que
garante que o arquivo é o validado.

| Classe do AudioSet | Tipo do evento | Severidade |
| --- | --- | --- |
| Cough | `tosse` | baixa |
| Breathing | `respiracao` | baixa |
| Wheeze | `sibilo` | média |
| Gasp | `arfar` | média |
| Pant | `respiracao_ofegante` | média |

Um **evento** é um trecho contínuo de quadros com o score da classe acima do mínimo, e leva o score
máximo e o médio.

| Parâmetro | Valor | Origem |
| --- | --- | --- |
| Score mínimo | **0,30** | `[EXP]` |
| Lacuna tolerada dentro de um trecho | 1 quadro | |
| Máximo de eventos por áudio | 20 (ficam os de maior score) | evita inundar o painel com uma respiração contínua |
| Fala do médico | quadros cujo centro cai em fala do médico são descartados | só com transcrição |

A tabela de classes do YAML é **conferida contra o mapa de 521 nomes** no carregamento. Sem o modelo
baixado, a análise segue sem o critério respiratório e o relatório registra
`respiratorio.disponivel = false`, em vez de falhar ou de fingir que não houve evento.

### 3.6 Baseline do paciente (US12)

`audio/vocal/baseline.py`. Com pelo menos **3 áudios anteriores** do mesmo paciente (pelo pseudônimo), cada
métrica é comparada com a mediana do histórico por um **z robusto**, o mesmo critério usado nos sinais
vitais (US15):

`z = (x − mediana) / max(1,4826 · MAD, piso da métrica)`

Um desvio com `|z| ≥ 3,5` `[EXP]` gera `desvio_baseline_vocal`, de severidade baixa (média com 3 ou
mais métricas fora). O **piso** da dispersão evita um z enorme quando o histórico é quase constante:

| Métrica | Piso | Métrica | Piso |
| --- | --- | --- | --- |
| `jitter_local_pct` | 0,10 | `intensidade_db` | 2,0 |
| `shimmer_local_pct` | 0,50 | `proporcao_pausas` | 0,05 |
| `hnr_db` | 1,0 | `taxa_fala_pps` | 0,2 |
| `f0_media_hz` | 5,0 | `taxa_articulacao_pps` | 0,2 |
| `f0_desvio_st` | 0,5 | | |

O histórico guarda no máximo 30 áudios, em um JSON por paciente indexado **só pelo pseudônimo**,
gravado de forma atômica. Um áudio que disparou uma classe **não entra** na baseline, para que uma
alteração não vire o novo normal. A baseline também alimenta o critério `intensidade_caiu` (queda de
6 dB em relação à mediana do paciente).

### 3.7 Classificação (US14)

`audio/vocal/classificacao.py`. Cada critério é um predicado sobre os biomarcadores, com limiar vindo
do YAML:

| Critério | Satisfeito quando | Origem |
| --- | --- | --- |
| `jitter_alto` | jitter local > **1,04 %** | `[MDVP]` |
| `shimmer_alto` | shimmer local > **3,81 %** | `[MDVP]` |
| `hnr_baixo` | HNR < **20 dB** | `[MDVP]` |
| `pausas_altas` | pausas ocupam > **40 %** da fala | `[EXP]` |
| `fala_lenta` | taxa de fala < **1,8** palavras/s (≈ 108 por minuto) | `[EXP]` |
| `articulacao_lenta` | taxa de articulação < **2,2** palavras/s | `[EXP]` |
| `f0_monotona` | desvio de F0 < **2,0** semitons | `[EXP]` |
| `intensidade_caiu` | intensidade ≥ **6 dB** abaixo da baseline do paciente | `[EXP]` |
| `eventos_respiratorios` | ≥ **2** eventos, ou um com score ≥ **0,60** | `[EXP]` |

Valores exatamente no limiar não contam como alteração. Um critério **sem dado** (por exemplo, taxa de
fala sem transcrição, ou intensidade sem baseline) fica "não avaliável" e **não conta a favor**.

Uma classe dispara com `minimo` critérios satisfeitos e, quando há, pelo menos um de `exigir_um_de`:

| Classe (`event_type`) | Critérios | Mínimo | Exige um de | Severidade |
| --- | --- | --- | --- | --- |
| `dificuldade_respiratoria` | eventos respiratórios, pausas altas, fala lenta, HNR baixo | 2 | eventos respiratórios ou pausas altas | média; **alta com 3** |
| `fadiga_vocal` | intensidade caiu, F0 monótona, pausas altas, fala lenta | 2 | n/a | média |
| `possivel_disartria` | jitter, shimmer, HNR, articulação lenta, pausas altas | 3 | articulação lenta ou pausas altas | média |
| normal | nenhuma classe disparou (e nenhum outro achado) | n/a | n/a | `sem_achados` (info) |

Uma classe pode disparar junto com outra: **cada uma vira um evento**, com os critérios que a explicam.
O `score` do evento é a fração de critérios satisfeitos (3 de 4 = 0,75). A única saída de severidade
**alta**, a de maior prioridade no motor de alertas, é `dificuldade_respiratoria` com 3 critérios; as
outras classes param em severidade média, por desenho.

**Explicabilidade.** Cada achado de classe traz o `summary` com cada critério satisfeito (valor e
limiar), as `features` com as medições e as `regras_acionadas`, os `thresholds` que a classe usa mais a
versão da configuração, os `models` (Praat, parselmouth, YAMNet, Azure Speech) e o artefato
`audio_segment`. O `summary` sempre começa com "Indicativo para avaliação clínica, não é diagnóstico",
e `features.aviso` repete a frase, porque o e-mail de alerta lista as features.

### 3.8 Contrato e alertas

Os seis `event_type` de áudio já existiam no catálogo do contrato; **nada mudou no catálogo nem no
schema**.

| Achado | `EventoAchado` |
| --- | --- |
| classe disparada (US14) | `fadiga_vocal`, `dificuldade_respiratoria` ou `possivel_disartria` |
| trecho do YAMNet (US12) | `evento_respiratorio`, com o `score` do trecho |
| métricas fora da baseline (US12) | `desvio_baseline_vocal` |
| nenhuma classe e nada mais | `sem_achados` (info): é a classe "normal" |

Campos comuns: `patient_id` pseudonimizado; `timestamp` = `recorded_at` + posição do trecho no áudio;
`window` com início e fim; `model_version = audio-achados@0.1.0`; `context.source_id = audio_id`;
URIs de artefato **relativas**. Os valores numpy viram `float`/`int` nativos e NaN vira `None`.

A publicação vai ao **barramento compartilhado** (`app.barramento.get_barramento()`, importado no nível
do módulo para ser substituível nos testes). Um evento de severidade alta vira alerta sem mudar o
motor, com prioridade `alta` e origem `padrao:alta`.

**Não há regra de áudio em `config/alertas_regras.yaml`, de propósito.** Uma regra por tipo
*substituiria* a prioridade e anularia a lógica de "2 critérios = média, 3 = alta".

### 3.9 Interfaces

| Interface | Uso |
| --- | --- |
| `python -m audio.ingestao.run --input <pasta> --origem ... --licenca ...` | só a US10: converte uma pasta, um arquivo ruim não interrompe o lote |
| `python -m audio.vocal.run --audio <wav> --patient-id <id> --recorded-at <ISO UTC> [--transcrever --medico-falante N]` | US10 + US12 + US14 de ponta a ponta; liga o motor de alertas. Saída `0`, `1` (configuração) ou `2` (algum áudio falhou) |
| `POST /audio/vocal` (multipart) | o mesmo pela API; `?detalhes`, `?publicar` e `?atualizar_baseline` |
| `python scripts/baixar_modelos_audio.py` | baixa o YAMNet em ONNX, com SHA-256 conferido |
| `python scripts/avaliar_us14.py` | avaliação em casos injetados (seção 4) |

A rota valida tudo o que não depende do áudio (formato, metadados, configuração, chave de
pseudonimização, credenciais do Speech) **antes** de gravar o upload e antes de gastar uma chamada ao
Speech. Respostas de erro: `400` formato não suportado, `409` `audio_id` já usado, `413` acima do
limite de tamanho, `422` áudio ou análise inválidos, `503` configuração ou chave ausente. O áudio
enviado é preservado em `saida/audio/<audio_id>/original.<ext>`.

## 4. Resultados

Os números abaixo vêm de testes automatizados, da avaliação em casos injetados
(`saida/audio/avaliacao_us14.json`) e de uma rodada com áudios reais. Cada linha diz de qual das três
fontes veio.

| US | Métrica | Meta | Obtido | Atingida? | Observação |
| --- | --- | --- | --- | :---: | --- |
| US10 | Entradas convertidas para WAV PCM 16 kHz, 16 bits, mono | todas as válidas | cada conversão é relida e validada. Testado de 8 a 48 kHz, mono e estéreo, OGG/Vorbis e OGG/Opus, sem ffmpeg | ✔ | `tests/audio/test_ingestao_conversao.py` |
| US10 | Áudio ruim não interrompe o lote | sim | erro tipado em log e em `metadados.csv`; a CLI sai com 2 | ✔ | `test_ingestao_lote.py`, `test_ingestao_validacao.py` |
| US10 | Normalização e corte de silêncio só na cópia; original preservado | sha256 igual | `original_preservado` por áudio; `convertido.wav` mantém pausas e volume | ✔ | `test_ingestao_preparo.py` |
| US10 | Nenhum áudio versionado | 0 | 0 (teste roda `git ls-files`) | ✔ | |
| US12 | Features por segmento do paciente | F0, jitter, shimmer, HNR, intensidade, taxa de fala, pausas | todas implementadas | ✔ | taxa de fala exige transcrição |
| US12 | F0 em voz sintética | n/a | 148,8 Hz medido contra 148,35 Hz esperado (0,3 %) | n/a | `test_vocal_biomarcadores.py` |
| US12 | Jitter injetado de 2 % | n/a | 1,96 % medido | n/a | |
| US12 | Limiares MDVP documentados | jitter 1,04 %, shimmer 3,81 %, HNR 20 dB | no YAML, marcados `[MDVP]` | ✔ | com ressalva: áudio comprimido (seção 5.2) |
| US12 | Eventos respiratórios com modelo do AudioSet e score por segmento | sim | YAMNet real roda: silêncio gera `Silence` = 1,0; tom de 440 Hz gera `Telephone`/`Dial tone`; a conta de quadros fecha | ✔ | `test_vocal_respiratorio.py` |
| US12 | YAMNet em tosse ou respiração **reais** | sem meta | não medido | ✘ | não há gravações rotuladas no projeto |
| US12 | Baseline do próprio paciente | ≥ 3 áudios | implementada, com z robusto e piso por métrica | ✔ | `test_vocal_baseline.py` |
| US14 | Recall por classe (casos injetados) | ≥ 0,80 | fadiga **1,00** · dificuldade respiratória **1,00** · disartria **1,00** (IC95 0,94 a 1,00; 60 casos cada) | ✔ | **avaliação circular** (ver abaixo) |
| US14 | Voz normal sintética sem nenhuma classe disparada | n/a | **60/60**; falso positivo 0/60 (0 %) | n/a | |
| US14 | Achado de severidade alta chega ao motor de alertas | sim | `dificuldade_respiratoria` com 3 critérios gera alerta `padrao:alta` | ✔ | `test_vocal_analise.py` |
| US14 | Ao menos 1 exemplo de alteração vocal detectada | 1 | 1 caso **sintético** (seção 5.1) | ✔ (sintético) | os 3 áudios reais não dispararam classe |
| Todos | Testes automatizados do módulo | n/a | 227 funções de teste em 16 arquivos | n/a | `tests/audio/`; sem rede e sem pesos (o YAMNet real é opcional) |

### Avaliação em casos injetados (US14)

`scripts/avaliar_us14.py`: 3 sementes × 20 casos por classe (**60 por classe**), voz sintética passando
pelo pipeline inteiro (ingestão, Praat, regras). Um caso é acerto quando a classe do rótulo dispara
(outras podem disparar junto); em "normal", quando nenhuma dispara. Meta de recall: 0,80.

| Rótulo | Recall (IC 95 %) | Classes que dispararam |
| --- | --- | --- |
| normal | 1,00 [0,94; 1,00] | nenhuma em 60/60 |
| `fadiga_vocal` | 1,00 [0,94; 1,00] | fadiga 60, dificuldade respiratória 26 |
| `dificuldade_respiratoria` | 1,00 [0,94; 1,00] | dificuldade 60, fadiga 58, disartria 52 |
| `possivel_disartria` | 1,00 [0,94; 1,00] | disartria 60, fadiga 25 |

As faixas de parâmetros de cada rótulo (jitter, shimmer, SNR, variação de F0, ritmo de fala, pausas)
descrevem a alteração pretendida e **não foram ajustadas até as regras acertarem**. Mesmo assim:

- **A avaliação é circular.** Os limiares experimentais e os parâmetros injetados são dos mesmos
  autores, e os casos foram injetados bem acima dos limiares. O recall de 100 % mostra que as regras e a
  implementação respondem às alterações que procuram, **não** que o sistema acerta em pacientes. Falta
  voz real rotulada por fonoaudiólogo.
- **O YAMNet não é avaliado.** Os eventos respiratórios entram como scores injetados
  (`sessao_sintetica`), então só a lógica das regras é testada.
- **As classes se sobrepõem.** Fadiga e dificuldade respiratória compartilham fala lenta e pausada, e
  voz soprosa parece disártrica na acústica. É a leitura mais útil da tabela.

### Rodada com áudio real

Três áudios de voz (OGG/Opus, 5,6 a 8,4 s, 16 kHz mono) rodaram pela CLI, sem rede e sem transcrição
(portanto sem taxa de fala):

| Amostra | F0 | Jitter local | Shimmer local | HNR | Pausas |
| --- | --- | --- | --- | --- | --- |
| 1 | 105 Hz | 2,31 % | 14,8 % | 12,7 dB | 16 % |
| 2 | 94 Hz | 2,02 % | 12,1 % | 14,0 dB | 7 % |
| 3 | 97 Hz | 1,97 % | 10,9 % | 13,7 dB | 30 % |

Os três ultrapassam os três limiares MDVP ao mesmo tempo e saíram `normal` apenas porque a disartria
exige fala lenta ou pausas, e sem transcrição isso não é avaliável. Três amostras com o mesmo padrão
indicam **artefato**, não três alterações (seção 5.2).

### Metas não atingidas e por quê

- **Validação clínica dos limiares.** Os limiares `[EXP]` (pausas, taxa de fala e de articulação,
  variação de F0, queda de intensidade, score respiratório) não têm validação. Dependem de gravações
  rotuladas por fonoaudiólogo, que o projeto não tem.
- **Exemplo de alteração em áudio real.** Nenhum dos 3 áudios reais disparou uma classe, então o exemplo
  documentado é sintético. **Para o vídeo de demonstração falta uma gravação real alterada.**
- **YAMNet em sons reais.** Foi verificado em silêncio e em tons, não em tosse ou respiração reais. O
  score mínimo de 0,30 é experimental, e o TensorFlow avisa que os scores do YAMNet não são
  probabilidades calibradas entre classes.
- **Transcrição real com o Azure Speech.** O caminho com `--transcrever` não foi exercitado contra o
  serviço real nesta entrega: a assinatura Azure do grupo está desativada (ver o
  [relatório técnico geral](relatorio_tecnico.md), seção 4). Os testes usam uma transcrição sintética no
  formato da US11 e um transcritor substituível.
- **Alertas.** Na rodada do exemplo da seção 5.1, o alerta foi registrado no painel mas ficou `failed`
  ao enviar, porque nenhum canal (SMTP ou webhook) estava configurado no ambiente.

## 5. Exemplos de achados

### 5.1 Dificuldade respiratória de severidade alta (caso sintético)

Voz sintética de dificuldade respiratória (3 frases curtas, pausas longas, voz soprosa), pela CLI, com
o YAMNet e o Praat reais e o motor de alertas ligado. Nenhum evento respiratório passou do score
mínimo; o achado veio de **3 de 4 critérios**:

```json
{"modality": "audio", "event_type": "dificuldade_respiratoria", "severity": "alta", "score": 0.75,
 "model_version": "audio-achados@0.1.0",
 "evidence": {"summary": "Indicativo para avaliação clínica, não é diagnóstico: padrão compatível com dificuldade respiratória (3 de 4 critérios): pausas ocupam 50% da fala (limite 40%); taxa de fala 0.92 palavras/s (mínimo 1.8 palavras/s); HNR 18.17 dB (mínimo 20 dB)",
   "features": {"regras_acionadas": "pausas_altas, fala_lenta, hnr_baixo", "proporcao_pausas": 0.499,
                "pausa_media_s": 1.901, "taxa_fala_pps": 0.919, "taxa_articulacao_pps": 1.833,
                "hnr_db": 18.17, "jitter_local_pct": 0.519, "shimmer_local_pct": 2.635,
                "f0_media_hz": 122.0, "eventos_respiratorios": 0,
                "aviso": "Indicativo para avaliação clínica; não é diagnóstico."},
   "thresholds": {"versao_limiares": "1.0.0", "minimo_criterios": 2, "criterios_forte": 3,
                  "proporcao_pausas_max": 0.4, "taxa_fala_pps_min": 1.8, "hnr_db_min": 20.0,
                  "eventos_respiratorios_min": 2, "score_respiratorio_forte": 0.6},
   "models": {"praat": "6.1.38", "parselmouth": "0.4.7", "yamnet": "yamnet-onnx",
              "azure-speech": "transcricao-rapida"}}}
```

O motor de alertas gerou o alerta com prioridade `alta`, origem `padrao:alta` e a ação sugerida
"Avaliar o paciente agora, conforme o protocolo do setor." Na mesma rodada dispararam também
`fadiga_vocal` e `possivel_disartria` (severidade média): a sobreposição descrita na seção 4. Valores e
limiares aparecem no próprio evento, então quem recebe o alerta vê o que o disparou e com qual versão
de limiar.

### 5.2 Áudio real do WhatsApp: três amostras fora dos limiares, classificadas como normais

Os três áudios reais (OGG/Opus) ultrapassaram **jitter, shimmer e HNR ao mesmo tempo** (jitter ≈ 2 %,
shimmer de 11 a 15 %, HNR de 13 a 14 dB). O padrão idêntico nas três amostras aponta para artefato:
codec com perdas, gravação de celular e fala encadeada, quando os limiares MDVP são de gravação de
qualidade clínica, em geral de vogal sustentada. O sistema reagiu de três formas:

- só saíram `normal` porque a disartria exige também fala lenta ou pausas;
- o achado leva `audio_comprimido = true` (formatos com perdas: `ogg, oga, opus, mp3, m4a, aac, wma,
  amr, webm`) e o relatório, uma nota de que jitter, shimmer e HNR podem estar superestimados;
- a conclusão registrada é que **estes limiares não devem ser aplicados a áudio de WhatsApp sem
  calibração**.

### 5.3 Um defeito que a avaliação encontrou

Na primeira avaliação com casos injetados, **60 de 60 casos de disartria também disparavam
"dificuldade respiratória"**: voz alterada (HNR baixo) mais fala lenta bastava, sem nenhum sinal
respiratório. A regra passou a exigir um evento respiratório (YAMNet) ou pausas frequentes (fala
entrecortada para respirar), o parâmetro `exigir_um_de`, e a sobreposição caiu a 0/60 sem perder recall.
Como a medição seguinte usa os mesmos dados, essa melhora também é circular. O motivo da regra é
clínico, não o número: dificuldade respiratória precisa de sinal respiratório ou de fala entrecortada.

## 6. Dados, licenças e LGPD

### Dados e componentes

Nenhum áudio é versionado. Os testes geram voz sintética em `tmp_path`, e o `.gitignore` cobre
`tratamento_audio/` e as extensões `*.wav *.ogg *.oga *.opus *.m4a *.mp3 *.flac *.aac *.wma *.amr`.
Não há conjunto público de voz patológica rotulada que o projeto possa versionar.

| Fonte | Uso | Licença |
| --- | --- | --- |
| Voz sintética (`audio/vocal/injecao.py`) | testes e avaliação da US14 | gerada pelo grupo; nada a redistribuir |
| 3 áudios de voz (OGG/Opus) | rodada com áudio real | uso local, não versionados. O consentimento do titular precisa ser confirmado (o texto padrão de proveniência do módulo diz isso) |
| YAMNet em ONNX, espelho de terceiros [`andrelgomes/yamnet-onnx`](https://huggingface.co/andrelgomes/yamnet-onnx) (conversão do modelo do Google) | eventos respiratórios | Apache-2.0, conforme o cabeçalho do script de download. SHA-256 fixado; é uma conversão de terceiros, e a alternativa é converter do TF Hub com `tf2onnx` |
| `yamnet_class_map.csv` (`tensorflow/models`) | nomes das 521 classes | Apache-2.0 |
| Praat, via `praat-parselmouth` 0.4.7 | biomarcadores | **GPL-3.0 ou posterior** (parselmouth; o Praat é GPL-2.0 ou posterior). A GPL vale para quem redistribuir o software: avaliar antes de qualquer distribuição |
| `soundfile` (libsndfile), `scipy`, `onnxruntime` | leitura, reamostragem e inferência | licenças permissivas (BSD, MIT) e LGPL no libsndfile; conferir antes de redistribuir |

### Tratamento LGPD

A voz de um paciente é dado pessoal sensível (LGPD, art. 5º, II). As medidas do módulo:

| Medida | Como |
| --- | --- |
| Pseudonimização na borda (art. 13 §4º) | `patient_id` vira `pt_<24 hex>` por HMAC-SHA256 com `PSEUDONYM_KEY` (`contratos/pseudonimizacao.py`) antes de qualquer cálculo. O ID de origem nunca vai para o disco nem para a resposta: o JSON da transcrição é gravado com o pseudônimo, e a baseline é indexada só por ele (um nome de arquivo fora do padrão é recusado) |
| Nada de áudio no Git | `.gitignore` e um teste que roda `git ls-files` e falha se achar uma extensão de áudio |
| Minimização e evidência por referência | o evento guarda a URI relativa do trecho (`audio_segment`), não o conteúdo; os metadados guardam o nome do arquivo (relativo à pasta de entrada) e nunca o caminho absoluto |
| Proveniência | cada áudio registra `origem` e `licenca` (`--origem`, `--licenca`); o padrão avisa que o consentimento (LGPD) do titular deve ser confirmado antes do uso |
| Processamento local | Praat, YAMNet e as regras rodam na máquina. Só a transcrição **opcional** (`--transcrever`) envia o áudio ao Azure Speech; região e termos em [relatório geral](relatorio_tecnico.md), seção 6 |
| Segurança do `audio_id` | ele vira nome de pasta. O padrão do contrato aceitaria `..`, que sairia da pasta de saída (e o tratamento de erro apagaria um diretório pai). Exige-se que comece por letra ou número, e um `audio_id` repetido responde `409` em vez de sobrescrever o original preservado |
| Limite de upload | a API recusa upload acima do limite configurado (`AUDIO_MAX_UPLOAD_MB`, `413`) e apaga o que gravou |
| Auditoria | `eventos.jsonl` e o barramento são JSONL de acréscimo; `original_preservado` e o sha256 provam que o original não mudou |

Um uso clínico real exigiria base legal (art. 11), relatório de impacto (RIPD), contrato de operador
com o provedor da transcrição e política de retenção, e o grupo não fez nenhum deles.

## 7. Limitações e trabalhos futuros

**Não é dispositivo médico validado clinicamente.** Nenhum limiar, prioridade ou regra foi revisado por
equipe clínica nem avaliado prospectivamente. Uso assistencial exigiria validação clínica e
enquadramento regulatório (ANVISA, RDC 657/2022, software como dispositivo médico).

**Limitações**

- **Áudio comprimido e fala encadeada.** Os limiares MDVP são de gravação de qualidade clínica. Em
  formatos com perdas (Opus do WhatsApp, MP3), os três limiares estouram juntos (seção 5.2). O achado
  leva `audio_comprimido`, mas o sistema **não corrige** o efeito.
- **Sem supressão de ruído.** O módulo só se protege do ruído (ganho máximo, piso de nível, HNR; seção
  3.2). Ruído de fundo reduz o HNR e pode ser lido como voz alterada. É o comportamento esperado da
  medida; não foi quantificado.
- **Limiares `[EXP]` sem validação.** E a origem primária dos limiares `[MDVP]` (1,04 %, 3,81 %, 20 dB)
  vem do texto da US12; este relatório não a conferiu em uma fonte primária.
- **Avaliação circular e sintética.** A US14 mede a lógica das regras em voz sintética com alterações
  injetadas por quem escolheu os limiares. Não mede validade clínica nem o YAMNet.
- **Sobreposição de classes.** Fala lenta e pausada é comum a fadiga e dificuldade respiratória, e voz
  soprosa parece disártrica na acústica.
- **Intensidade** é relativa ao ganho do microfone e só serve entre gravações em condições parecidas.
- **YAMNet.** Verificado em silêncio e em tons, não em sons respiratórios reais. As classes do AudioSet
  são genéricas: o modelo não sabe de quem é a tosse, e só descarta a fala do médico quando há
  transcrição com diarização. Os scores não são probabilidades, e o modelo ONNX é uma conversão de
  terceiros.
- **Sem transcrição, sem taxa de fala.** Os critérios `fala_lenta` e `articulacao_lenta` ficam não
  avaliáveis, e a disartria, que exige 3 critérios incluindo um de articulação ou pausas, quase não
  dispara. Já a taxa de fala em palavras por segundo depende do idioma e do tipo de fala.
- **Papel assumido.** Áudio de um só falante é tratado como paciente sem verificação. A diarização é de
  até 2 falantes.
- **Baseline com poucos áudios.** O mínimo é 3, e a baseline de um paciente ainda pode ser formada por
  gravações em condições diferentes. A trava do arquivo é por processo, então a API deve rodar com um
  único worker.
- **Processamento síncrono.** A rota processa o áudio dentro da requisição. Não há tempo real nem
  *streaming* de áudio.
- **Vários `--audio` na CLI** compartilham o mesmo `--recorded-at`; gravações em horários diferentes
  pedem uma execução por gravação.

**Trabalhos futuros**

1. **Calibração e validação clínica** dos limiares com gravações rotuladas por fonoaudiólogo, separando
   por tipo de tarefa (vogal sustentada ou fala encadeada) e por codec.
2. **Avaliar o YAMNet em tosse e respiração reais** e, se preciso, treinar um classificador dedicado a
   sons respiratórios.
3. **Estimativa de SNR e supressão de ruído** como etapa opcional, com um portão de qualidade que avise
   quando a gravação não serve para medir jitter, shimmer e HNR.
4. **Rodar a transcrição real** (Azure Speech) quando a assinatura for reativada, e medir o efeito da
   taxa de fala nas classes.
5. **Biomarcadores adicionais** (openSMILE, conjunto eGeMAPS) para comparar com o Praat.
6. **Fusão multimodal (US18):** usar os eventos de áudio com sinais vitais e vídeo em
   `risco_multimodal`.
7. **Baseline em banco de dados** e processamento assíncrono para suportar vários *workers* e
   gravações longas.
8. **Estudo retrospectivo** com eventos reais rotulados e revisão dos limiares e prioridades pela
   equipe clínica.

## 8. Referências

1. Boersma, P.; Weenink, D. *Praat: doing phonetics by computer.* https://www.praat.org (versão 6.1.38,
   a que o parselmouth 0.4.7 embute).
2. Jadoul, Y.; Thompson, B.; de Boer, B. Introducing Parselmouth: A Python interface to Praat. *Journal
   of Phonetics*, 71, 1-15, 2018. https://doi.org/10.1016/j.wocn.2018.07.001
3. Teixeira, J. P.; Oliveira, C.; Lopes, C. Vocal acoustic analysis: jitter, shimmer and HNR
   parameters. *Procedia Technology*, 9, 1112-1122, 2013.
4. Gemmeke, J. F. et al. Audio Set: An ontology and human-labeled dataset for audio events. *IEEE
   ICASSP*, 2017.
5. Plakal, M.; Ellis, D. *YAMNet.* TensorFlow Models, `research/audioset/yamnet`.
   https://github.com/tensorflow/models/tree/master/research/audioset/yamnet
6. Iglewicz, B.; Hoaglin, D. *How to Detect and Handle Outliers* (z-score modificado com MAD). ASQC,
   1993.
7. IEC 60601-1-8:2006+A1:2012+A2:2020. *Medical electrical equipment, Part 1-8: General requirements
   for basic safety and essential performance. Collateral standard: General requirements, tests and
   guidance for alarm systems in medical electrical equipment and medical electrical systems.*
8. Microsoft. *Fast transcription* (Azure AI Speech). Microsoft Learn. Detalhes da transcrição (US11)
   no [relatório técnico geral](relatorio_tecnico.md).
9. Brasil. Lei nº 13.709/2018 (Lei Geral de Proteção de Dados Pessoais). ANVISA, RDC nº 657/2022
   (software como dispositivo médico).
