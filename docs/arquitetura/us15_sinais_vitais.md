# US15 · Detecção de anomalias em sinais vitais

Pacote `sinais/`. Uma medição entra por vez (streaming) e passa por três camadas:

```
medição ─► limpeza ─► NEWS2 (regras clínicas) ─► baseline do paciente (ML) ─► EventoAchado
```

| Arquivo | Papel |
|---|---|
| `sinais/limpeza.py` | Faixas fisiológicas (`FAIXAS`) e validade do último valor (`VALIDADE`) |
| `sinais/news2.py` | Pontos por parâmetro, total e gatilhos (RCP 2017, SpO2 escala 1) |
| `sinais/detector.py` | `DetectorSinais.processar(medicao)`: estado por paciente, janelas deslizantes, eventos |
| `sinais/mimic.py` | Leitura do MIMIC-IV Demo (`chartevents`) para o formato longo |
| `sinais/avaliacao.py` | Injeção de anomalias rotuladas e matriz de confusão |
| `app/routers/sinais_vitais.py` | API (`/sinais-vitais/news2` e `/sinais-vitais/medicoes`) |

## Dados

MIMIC-IV Clinical Database Demo 2.2 (PhysioNet, acesso aberto, 100 pacientes de UTI,
~77 mil medições). `python scripts/baixar_sinais.py` grava em `dados/mimic-iv-demo/` (fora do Git).

| Sinal | itemid | Observação |
|---|---|---|
| FC (`fc`) | 220045 | |
| PA sistólica (`pas`) | 220179, 220050 | não invasiva e arterial na mesma série |
| PA diastólica (`pad`) | 220180, 220051 | idem |
| SpO2 (`spo2`) | 220277 | escala 1 |
| FR (`fr`) | 220210 | |
| Temperatura (`temp`) | 223762, 223761 | °F convertido para °C |
| O2 suplementar (`o2_suplementar`) | 223834 (O2 Flow) | fluxo > 0 L/min = sim |
| Consciência (`alerta`) | 220739 (GCS abertura ocular) | 4 (espontânea) = Alerta; 3/2/1 ≈ V/P/U |

A amostragem é de ~1 h (mediana do intervalo entre FCs = 60 min); a temperatura vem a cada ~4 h.

## Limpeza de artefatos

Valor fora da faixa fisiologicamente possível é **descartado** antes de qualquer cálculo e
contado em `DetectorSinais.artefatos` por sinal. Valor ausente, `NaN` ou infinito também.

| Sinal | Faixa aceita | Exemplo descartado |
|---|---|---|
| FC | 20–300 bpm | FC > 300, FC = 0 |
| PAS | 40–300 mmHg | 0, 999 |
| PAD | 15–200 mmHg | |
| SpO2 | 50–100 % | SpO2 = 0 (sensor solto) |
| FR | 3–70 irpm | |
| Temperatura | 25–45 °C | 0 °C, °F digitado no campo °C |

No MIMIC Demo (dados reais, `scripts/rodar_us15.py`) foram descartados 57 valores: FR 47, FC 3, SpO2 3,
Temp 3, PAS 1.

Ficou de fora a checagem cruzada PAD ≥ PAS, porque ela exige parear as duas medições no mesmo instante.

## Dados faltantes

Não há interpolação. O último valor válido de cada parâmetro continua valendo para o NEWS2
por um tempo limitado (`VALIDADE`):

| Parâmetro | Validade |
|---|---|
| FC, PA, SpO2, FR | 2 h |
| Temperatura | 6 h |
| O2 suplementar, consciência | 8 h |

Depois desse prazo o parâmetro fica **ausente**: não pontua, e o evento sai com
`news2_parcial: true` e `news2_ausentes` listando o que faltou. Assim um NEWS2 calculado com
dados incompletos nunca parece completo. Na camada de ML, um intervalo sem medições maior que a
janela de baseline (12 h) esvazia a baseline, e o detector volta ao aquecimento (6 medições)
antes de avaliar desvios.

## Camada clínica: NEWS2

`news2.calcular` aplica a tabela de referência da US15 por parâmetro e soma o total.

| Gatilho | Regra | `event_type` | `severity` |
|---|---|---|---|
| Resposta urgente | total 5–6 **ou** 3 pontos em um parâmetro | `news2_resposta_urgente` | `media` |
| Emergência | total ≥ 7 | `news2_emergencia` | `alta` |

O evento é emitido quando o nível **sobe** (nenhum → urgente → emergência). Enquanto o nível
se mantém, não há repetição. Se o nível cair e subir de novo, um novo evento é emitido.
`score = total / 20`.

## Camada de ML: baseline do próprio paciente

Não supervisionada, por paciente e por sinal contínuo (FC, PAS, PAD, SpO2, FR, Temp). Não usa
o Azure AI Anomaly Detector, que foi aposentado em 01/10/2026: tudo roda localmente com numpy.

- **Mudança súbita (`desvio_baseline`):** z-score robusto
  `z = (x − mediana) / max(1,4826·MAD, escala mínima)` sobre a janela de baseline (12 h).
  Dispara com |z| > 2,5 em 2 medições seguidas. Valores em desvio não entram na baseline, que
  fica congelada durante o episódio e não "aprende" a anomalia. A escala mínima (ex.: FC 4 bpm,
  SpO2 1 %) evita z enorme em sinais quase constantes.
- **Tendência (`tendencia_deterioracao`):** regressão linear nas últimas 6 h (≥ 4 pontos,
  cobrindo ≥ 3 h). Dispara quando a variação prevista pela reta passa de 2,5 escalas da baseline.
  O episódio termina quando a variação cai abaixo da metade do limiar.

Cada episódio gera um evento. `score = min(1, |z| / 10)` (ou a variação em escalas) e
`severity` segue `severidade_sugerida`, porque essa camada não tem regra clínica própria.

## Janela deslizante e tempo real

`DetectorSinais` guarda apenas estado incremental: `deque`s de 12 h e 6 h por paciente e sinal,
e o último valor de cada parâmetro. O processamento leva ~0,1 ms por medição. O mesmo código atende:

- **Replay em tempo real:** `python scripts/rodar_us15.py --velocidade 60`. As medições do
  MIMIC chegam na ordem de tempo, deslocadas para o instante da execução, e os eventos vão para
  `saida/sinais/eventos_us15.jsonl`.
- **API em streaming:** `POST /sinais-vitais/medicoes` mantém a baseline entre chamadas.
- **Avaliação:** `scripts/avaliar_us15.py` processa medição a medição, sem olhar o futuro.
