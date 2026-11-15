# Áudio: ingestão, biomarcadores vocais e achados (US10, US12, US14)

> Decisões de projeto dos pacotes `audio/ingestao` e `audio/vocal`. A rastreabilidade ao DoD está em
> [`relatorio_us10_dod.md`](../relatorio_us10_dod.md), [`relatorio_us12_dod.md`](../relatorio_us12_dod.md)
> e [`relatorio_us14_dod.md`](../relatorio_us14_dod.md).

**Todo resultado é um indicativo para avaliação clínica, não um diagnóstico.**

## Fluxo

```
áudio (qualquer formato)
  │  audio.ingestao
  ├─ lê: soundfile (WAV, FLAC, OGG/Opus) ou ffmpeg (MP3, M4A, AAC, WMA, AMR)
  ├─ convertido.wav ........ WAV PCM 16 kHz, 16 bits, mono (o que vai ao Azure Speech)
  ├─ features.wav .......... cópia com volume normalizado e silêncios longos removidos
  └─ metadados.json ........ duração, taxa, canais, origem, licença, sha256
  │  audio.vocal
  ├─ segmentos do paciente: JSON do Azure Speech (US11) ou, sem ele, VAD por energia
  ├─ Praat/parselmouth: F0, jitter, shimmer, HNR, intensidade, pausas, taxa de fala
  ├─ YAMNet (ONNX): tosse, respiração, sibilo, arfar, com score por trecho
  ├─ baseline do próprio paciente (mediana/MAD dos áudios anteriores)
  ├─ regras: fadiga_vocal | dificuldade_respiratoria | possivel_disartria | normal
  └─ EventoAchado (modalidade audio) → barramento → motor de alertas (US19)
```

## Qual sinal alimenta cada medida

A US10 manda normalizar o volume e remover silêncios longos **só na cópia usada para as features**.
Isso tem uma consequência que decide o desenho: a remoção de silêncio apaga justamente as pausas.

| Medida | Sinal | Por quê |
| --- | --- | --- |
| F0, jitter, shimmer, HNR | `features.wav` (recalculada em memória) | Não dependem do volume nem de pausas; a normalização ajuda o detector de F0 |
| Proporção e duração das pausas, taxa de fala e de articulação | `convertido.wav` | A cópia perderia as pausas longas |
| Intensidade | `convertido.wav` | A normalização apagaria a diferença entre gravações altas e baixas |
| Eventos respiratórios (YAMNet) | `convertido.wav` | Decisão de projeto, não medida: o ganho de até 5x da normalização amplificaria o ruído de fundo, e respiração é um som fraco que se confunde com ele |

Os segmentos da transcrição estão no tempo do `convertido.wav`. `CopiaFeatures.indice` (em
`audio/ingestao/preparo.py`) mapeia cada amostra do original para a cópia, e `recortar(inicio_s, fim_s)`
devolve o trecho equivalente. O `features.wav` gravado em disco é o artefato da mesma conta.

## Segmentos do paciente e a voz do médico

- Com a transcrição do Azure Speech (US11), cada `fala` do paciente é um segmento, com as palavras de
  que a taxa de fala precisa. `medico_falante` diz quem é o médico; **dois falantes sem essa
  informação são recusados**, porque errar mediria a voz do médico como a do paciente (mesma regra do
  `audio/text`).
- **Exceção:** áudio de **um só falante** (a gravação do próprio paciente, como um áudio de voz do
  WhatsApp) é tratado como paciente e marcado como `papel_assumido`.
- A lacuna entre duas falas do paciente só conta como pausa dele se **não houver fala do médico no
  meio** (`contiguo_ao_anterior`); uma troca de turno não é pausa.
- Eventos respiratórios cujo quadro cai em fala do médico são descartados.
- Sem transcrição, o VAD por energia faz os segmentos (pausas menores que 1 s ficam dentro do segmento)
  e **não há taxa de fala**: os critérios que dependem dela ficam "não avaliáveis" e não contam.

## Pausas e nível de fala

Pausa = silêncio de pelo menos 0,25 s, a 25 dB abaixo do **nível de fala do paciente** (percentil 95
dos RMS dos segmentos). O limiar relativo sozinho falha com silêncio ou ruído de ambiente, que viram
"a fala de si mesmos"; por isso existe um **piso absoluto** de nível (`nivel_minimo_fala_db`, -50 dBFS).
Esse caso foi achado por um teste (segmento de silêncio digital era contado como fala).

## Limiares

Ficam em `audio/vocal/config.yaml`, cada um marcado com a origem:

| Marca | Significado | Limiares |
| --- | --- | --- |
| `[MDVP]` | Normalidade do MDVP, citada na US12 | jitter local > 1,04 %, shimmer local > 3,81 %, HNR < 20 dB |
| `[PRAAT]` | Parâmetro padrão do Praat | faixa de F0 75-500 Hz, janelas de período e amplitude |
| `[EXP]` | **Experimental**, sem validação clínica | pausas, taxa de fala e de articulação, variação de F0, intensidade vs baseline, score respiratório |

## Regras das classes

Cada critério é um predicado sobre os biomarcadores (`audio/vocal/classificacao.py`). Uma classe
dispara com `minimo` critérios satisfeitos e, quando há, pelo menos um de `exigir_um_de`:

| Classe (`event_type`) | Critérios | Mínimo | Exige um de | Severidade |
| --- | --- | --- | --- | --- |
| `dificuldade_respiratoria` | eventos respiratórios, pausas altas, fala lenta, HNR baixo | 2 | eventos respiratórios ou pausas altas | média; **alta com 3** |
| `fadiga_vocal` | intensidade caiu, F0 monótona, pausas altas, fala lenta | 2 | - | média |
| `possivel_disartria` | jitter, shimmer, HNR, articulação lenta, pausas altas | 3 | articulação lenta ou pausas altas | média |
| `normal` | nenhuma classe disparou (e nenhum outro achado) | - | - | `sem_achados` (info) |

Um critério sem dado **não conta a favor**. A alta (que o motor de alertas envia) exige 3 critérios.
Uma classe pode disparar junto com outra; cada uma vira um evento, com os critérios que a explicam.

**Por que dificuldade respiratória exige sinal respiratório ou pausas.** Voz alterada com fala lenta
também é disartria. Na primeira avaliação com casos injetados, 60 de 60 casos de disartria dispararam
também "dificuldade respiratória". A exigência de evento respiratório (YAMNet) ou de pausas frequentes
(fala entrecortada para respirar) zerou essa sobreposição sem perder recall.

## Explicabilidade

Cada achado de classe traz: `summary` com os critérios satisfeitos, valor e limiar de cada um;
`features` com as medições e `regras_acionadas`; `thresholds` com os limiares que a classe usa e a
versão da config; `models` (Praat, parselmouth, YAMNet, Azure Speech); artefato `audio_segment`.

## Baseline do paciente

Com pelo menos 3 áudios anteriores do mesmo paciente (pseudônimo), cada métrica é comparada com a
mediana do histórico: `|x - mediana| / max(1,4826 · MAD, piso)` ≥ 3,5 gera `desvio_baseline_vocal`.
O piso (por métrica, no YAML) evita um z enorme quando o histórico é quase constante. Áudio que
disparou uma classe **não entra** na baseline. O histórico fica em `saida/audio/baseline/<pt_...>.json`.

## Segurança e privacidade

- Nenhum áudio é versionado: `.gitignore` cobre `tratamento_audio/` e as extensões de áudio, e um teste
  roda `git ls-files` e falha se achar algum.
- O `patient_id` de origem nunca vai para o disco nem para a resposta: o JSON da transcrição é gravado
  com o pseudônimo.
- `audio_id` vira nome de pasta. O padrão do contrato aceita `..`, que sairia da pasta de saída (e o
  tratamento de erro apagaria um diretório pai); por isso exige-se que comece por letra ou número, e
  um `audio_id` repetido responde 409 em vez de sobrescrever o original preservado.

## Limitações

1. **Áudio comprimido e fala encadeada.** Os limiares MDVP são de gravação de qualidade clínica, em
   geral de vogal sustentada. Em uma rodada com 3 áudios de voz do WhatsApp (Opus), os três ultrapassaram
   os três limiares ao mesmo tempo (jitter ≈ 2 %, shimmer 11-15 %, HNR 13-14 dB). Só saíram "normal"
   porque a disartria exige fala lenta ou pausas, e sem transcrição isso não é avaliável. Em formatos
   com perdas o achado leva `audio_comprimido=true` e o relatório, uma nota.
2. **Os limiares `[EXP]` não foram validados clinicamente.** Dependem de gravações rotuladas por
   fonoaudiólogo, que o projeto não tem.
3. **A avaliação é circular.** A US14 mede recall em voz sintética com alterações injetadas por quem
   escolheu os limiares. Mede a lógica das regras, não a validade clínica, e não mede o YAMNet (os
   eventos respiratórios entram como scores).
4. **Sobreposição de classes.** Fala lenta e pausada é comum a fadiga e dificuldade respiratória, e voz
   soprosa parece disártrica na acústica. O relatório de avaliação mostra quais classes disparam em cada
   rótulo.
5. **Intensidade** é relativa ao ganho do microfone; só serve entre gravações em condições parecidas.
6. **YAMNet** foi verificado em silêncio e em tons, não em tosse ou respiração reais (não há gravações
   rotuladas no projeto).
7. Se `--saida` ficar fora do repositório, os eventos não levam artefatos (o contrato exige URI relativa
   à raiz).
