# US12 · Biomarcadores vocais: rastreabilidade ao DoD

Pacote [`audio/vocal/`](../audio/vocal/). Desenho, fórmulas e limitações em
[`arquitetura/us12_biomarcadores_vocais.md`](arquitetura/us12_biomarcadores_vocais.md).

**Indicativo para avaliação clínica, não diagnóstico.**

## Como reproduzir

```bash
uv run python scripts/baixar_modelos_audio.py          # YAMNet em ONNX (opcional), com SHA-256 fixado
uv run --env-file .env python -m audio.vocal.run --audio voz.ogg --patient-id 10000032 \
  --recorded-at 2026-10-01T13:00:00Z
# consulta com médico e paciente (Azure Speech, diarização):
uv run --env-file .env python -m audio.vocal.run --audio consulta.wav --patient-id 10000032 \
  --recorded-at 2026-10-01T13:00:00Z --transcrever --medico-falante 1

uv run pytest tests/audio/test_vocal_biomarcadores.py tests/audio/test_vocal_respiratorio.py \
  tests/audio/test_vocal_baseline.py tests/audio/test_vocal_segmentos.py
```

## Itens do DoD

| # | Item | Situação | Evidência |
| --- | --- | --- | --- |
| 1 | Features por segmento de fala do paciente: F0 (média e variação), jitter local, shimmer local, HNR, intensidade, taxa de fala, proporção e duração das pausas | Atendido | `biomarcadores.medir_segmentos` mede cada segmento do paciente e `agregar` junta o áudio. F0 em Hz e em semitons; jitter e shimmer ponderados pela voz sustentada. Taxa de fala e de articulação vêm das palavras da transcrição do Azure Speech; **sem transcrição não há taxa de fala** (segmentos por VAD). Medidas que o Praat não consegue virar `None`, nunca NaN. `test_vocal_biomarcadores.py` |
| 2 | Ferramenta consolidada: Praat/parselmouth | Atendido | `praat-parselmouth` 0.4.7 (Praat 6.1.38), com `To PointProcess (periodic, cc)`, `Get jitter (local)`, `Get shimmer (local)` e `To Harmonicity (cc)`. Os testes usam voz sintética de parâmetros conhecidos: F0 medida em 148,8 Hz contra 148,35 Hz esperados pelo gerador (0,3 %), e jitter injetado de 2 % medido em 1,96 % |
| 3 | Limiares de referência documentados: jitter local > 1,04 %, shimmer local > 3,81 %, HNR < 20 dB | Atendido, com ressalva | No [`config.yaml`](../audio/vocal/config.yaml), marcados `[MDVP]`, com os demais marcados `[PRAAT]` ou `[EXP]` (experimental). Valores exatamente no limiar não contam como alteração. **Ressalva:** são de gravação de qualidade clínica; ver "Rodada com áudio real" |
| 4 | Eventos respiratórios (respiração ofegante, tosse, arfar) com modelo pré-treinado no AudioSet, com score por segmento | Atendido | YAMNet em ONNX (`respiratorio.py`): Cough, Breathing, Wheeze, Gasp, Pant. Um evento por trecho contínuo de quadros acima do score mínimo, com score máximo e médio, `evento_respiratorio` e artefato `audio_segment`. Quadros em fala do médico são descartados. Sem o modelo baixado a análise segue e o relatório diz `respiratorio.disponivel = false` |
| 5 | Mais de um áudio do mesmo paciente: comparação com a baseline do próprio paciente | Atendido | `baseline.py`: mediana e MAD dos áudios anteriores, z robusto ≥ 3,5 gera `desvio_baseline_vocal`. Histórico em `saida/audio/baseline/<pt_...>.json`, só pelo pseudônimo. `test_vocal_baseline.py` e o ponto a ponto em `test_vocal_analise.py` |

## O que foi verificado e o que não foi

- **Praat e as regras:** testados com voz sintética de jitter, shimmer, ruído (HNR), melodia de F0 e pausas
  controlados. Os valores medidos acompanham os injetados e separam voz normal de alterada.
- **YAMNet:** o modelo real roda (silêncio gera `Silence = 1,0`; um tom de 440 Hz gera `Telephone`/`Dial tone`;
  a conta de quadros fecha). **Não foi medido em tosse ou respiração reais**: o projeto não tem
  gravações rotuladas. O score mínimo de 0,30 é experimental.
- **O espelho ONNX do YAMNet** (Hugging Face, Apache-2.0) é uma conversão de terceiros do modelo do
  Google. O script de download fixa o SHA-256; se o espelho sair do ar, a alternativa é converter do
  TF Hub com `tf2onnx`.

## Rodada com áudio real

Três áudios de voz (OGG/Opus, 5,6 a 8,4 s, 16 kHz mono) rodaram pela CLI, sem rede e sem transcrição:

| Amostra | F0 | Jitter local | Shimmer local | HNR | Pausas |
| --- | --- | --- | --- | --- | --- |
| 1 | 105 Hz | 2,31 % | 14,8 % | 12,7 dB | 16 % |
| 2 | 94 Hz | 2,02 % | 12,1 % | 14,0 dB | 7 % |
| 3 | 97 Hz | 1,97 % | 10,9 % | 13,7 dB | 30 % |

Os três ultrapassam os três limiares MDVP ao mesmo tempo e saíram `normal` apenas porque a disartria
exige fala lenta ou pausas. Três amostras com o mesmo padrão indicam artefato, não três alterações:
codec com perdas (Opus), gravação de celular e fala encadeada (os limiares MDVP são de gravação de
qualidade clínica). Por isso o achado leva `audio_comprimido = true` e o relatório uma nota. **Estes
limiares não devem ser aplicados a áudio de WhatsApp sem calibração.** Nenhuma classe foi disparada.

## Limitações

Ver [as limitações do desenho](arquitetura/us12_biomarcadores_vocais.md#limitações): limiares `[EXP]`
sem validação clínica, intensidade relativa ao microfone, YAMNet não medido em sons reais, e áudio
comprimido.
