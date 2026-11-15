# US14 · Classificar alterações vocais e gerar achados de áudio: rastreabilidade ao DoD

Pacote [`audio/vocal/`](../audio/vocal/) (`classificacao.py`, `eventos.py`, `avaliacao.py`) e rota
`POST /audio/vocal`. Regras e limitações em
[`arquitetura/us12_biomarcadores_vocais.md`](arquitetura/us12_biomarcadores_vocais.md).

**Indicativo para avaliação clínica, não diagnóstico.**

## Como reproduzir

```bash
uv run --env-file .env python -m audio.vocal.run --audio voz.wav --patient-id 10000032 \
  --recorded-at 2026-10-01T13:00:00Z --transcricao saida/transcricao/voz.json --medico-falante 1
uv run python scripts/avaliar_us14.py        # 3 sementes x 20 casos por classe, ~2 min
uv run pytest tests/audio/test_vocal_classificacao.py tests/audio/test_vocal_analise.py \
  tests/audio/test_vocal_avaliacao.py tests/audio/test_vocal_api.py
```

Saída por áudio em `saida/audio/<audio_id>/`: `eventos.jsonl` (contrato `EventoAchado`) e
`relatorio.json` (critérios, valores, limiares). A avaliação grava `saida/audio/avaliacao_us14.json`.

## Itens do DoD

| # | Item | Situação | Evidência |
| --- | --- | --- | --- |
| 1 | Classes de saída: fadiga, dificuldade respiratória, possível disartria, normal | Atendido | `fadiga_vocal`, `dificuldade_respiratoria`, `possivel_disartria` e `normal`, que é `sem_achados` (info). Os tipos já existiam no catálogo; nada mudou no schema. Uma classe pode disparar junto com outra, cada uma em um evento. `test_vocal_classificacao.py` |
| 2 | Regras com limiares documentados; cada achado lista as features que levaram à decisão | Atendido | Regras e limiares em [`config.yaml`](../audio/vocal/config.yaml), com a origem de cada um. O evento traz `summary` com cada critério satisfeito (valor e limiar), `features` (medições e `regras_acionadas`) e `thresholds` (os que a classe usa, mais a versão). Texto do "termo" da US13 não entra: a explicação é acústica |
| 3 | Saída identificada como "indicativo para avaliação clínica", não diagnóstico | Atendido | O `summary` de todo evento de áudio começa com "Indicativo para avaliação clínica, não é diagnóstico" e `features.aviso` repete a frase (o e-mail de alerta lista as features). Também em `relatorio.json` e na resposta da API (`aviso`) |
| 4 | Achados de severidade alta enviados ao motor de alertas (US19) | Atendido | `dificuldade_respiratoria` chega a **alta com 3 critérios**; publicada no barramento compartilhado, vira alerta `padrao:alta` sem mudar o motor. Não há regra de áudio em `alertas_regras.yaml` de propósito: uma regra por tipo **substituiria** a prioridade e anularia a lógica de 2 critérios = média. `test_achado_alto_publicado_no_barramento_vira_alerta_com_o_aviso` e a rodada abaixo |
| 5 | Ao menos 1 exemplo de alteração vocal detectada, para o relatório e o vídeo | Atendido com **caso sintético** | Abaixo. Os 3 áudios reais disponíveis não dispararam classe (ver US12). **Para o vídeo falta uma gravação real alterada** |

## Exemplo documentado (caso sintético)

Voz sintética de dificuldade respiratória (3 frases curtas, pausas longas, voz soprosa), pela CLI, com o
YAMNet e o Praat reais e o motor de alertas ligado:

```text
event_type: dificuldade_respiratoria   severity: alta   score: 0.75   model_version: audio-achados@0.1.0
summary: Indicativo para avaliação clínica, não é diagnóstico: padrão compatível com dificuldade
         respiratória (3 de 4 critérios): pausas ocupam 50% da fala (limite 40%); taxa de fala 0.92
         palavras/s (mínimo 1.8 palavras/s); HNR 18.17 dB (mínimo 20 dB)
features: regras_acionadas "pausas_altas, fala_lenta, hnr_baixo" | proporcao_pausas 0.499 | pausa_media_s 1.901
          taxa_fala_pps 0.919 | taxa_articulacao_pps 1.833 | hnr_db 18.17 | jitter_local_pct 0.519
          shimmer_local_pct 2.635 | f0_media_hz 122.0 | eventos_respiratorios 0 | aviso "Indicativo para..."
thresholds: versao_limiares 1.0.0 | minimo_criterios 2 | criterios_forte 3 | proporcao_pausas_max 0.4
            taxa_fala_pps_min 1.8 | hnr_db_min 20.0 | eventos_respiratorios_min 2 | score_respiratorio_forte 0.6
models: praat 6.1.38 | parselmouth 0.4.7 | yamnet yamnet-onnx | azure-speech transcricao-rapida
alerta: priority alta | rule_origin padrao:alta | modalities [audio]
        suggested_action "Avaliar o paciente agora, conforme o protocolo do setor."
```

Na mesma rodada dispararam também `fadiga_vocal` e `possivel_disartria` (média): a sobreposição descrita
abaixo. O alerta ficou `failed` ao enviar apenas porque nenhum canal (SMTP, webhook) estava configurado
no ambiente; ele está registrado no painel.

## Avaliação em casos injetados

`scripts/avaliar_us14.py`: 3 sementes x 20 casos por classe (60 por classe), voz sintética passando pelo
pipeline inteiro (ingestão, Praat, regras). Meta de recall: 0,80.

| Rótulo | Recall (IC 95 %) | Classe principal certa | Classes que dispararam |
| --- | --- | --- | --- |
| normal | 1,00 [0,94; 1,00] | 60/60 | nenhuma em 60/60 |
| fadiga_vocal | 1,00 [0,94; 1,00] | 60/60 | fadiga 60, dificuldade respiratória 26 |
| dificuldade_respiratoria | 1,00 [0,94; 1,00] | 60/60 | dificuldade 60, fadiga 58, disartria 52 |
| possivel_disartria | 1,00 [0,94; 1,00] | 60/60 | disartria 60, fadiga 25 |

Falso positivo em voz normal: **0/60**.

**Como ler isto, sem exagero:**

- **A avaliação é circular.** Os limiares experimentais e os parâmetros injetados são dos mesmos autores,
  e os casos foram injetados bem acima dos limiares. O recall de 100 % mostra que as regras e a
  implementação respondem às alterações que procuram, **não** que o sistema acerta em pacientes. Falta
  voz real rotulada por fonoaudiólogo.
- **O YAMNet não é avaliado**: os eventos respiratórios entram como scores injetados.
- **As classes se sobrepõem**, e é a leitura mais útil: fadiga e dificuldade respiratória compartilham
  fala lenta e pausada; voz soprosa parece disártrica na acústica.
- **A avaliação achou um defeito, que foi corrigido.** Na primeira rodada, 60/60 casos de disartria
  também disparavam "dificuldade respiratória" (HNR baixo + fala lenta, sem nenhum sinal respiratório).
  A regra passou a exigir evento respiratório ou pausas frequentes (`exigir_um_de`), e a sobreposição
  caiu a 0/60 sem perder recall. Como a medição seguinte usa os mesmos dados, essa melhora também é
  circular; o motivo da regra é clínico (dificuldade respiratória precisa de sinal respiratório ou de
  fala entrecortada), não o número.

## Limitações

Áudio comprimido (Opus do WhatsApp, MP3) distorce jitter, shimmer e HNR; o achado leva `audio_comprimido`
e o relatório uma nota. Demais limitações em
[`arquitetura/us12_biomarcadores_vocais.md`](arquitetura/us12_biomarcadores_vocais.md#limitações). Os
limiares de prioridade do motor de alertas continuam provisórios, como indica o
[`alertas_regras.yaml`](../config/alertas_regras.yaml).
