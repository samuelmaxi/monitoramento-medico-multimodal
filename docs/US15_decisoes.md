# US15 — Detecção de anomalias em sinais vitais: registro de decisões

Este documento registra as decisões de projeto da US15 para o relatório técnico.
Cada etapa acrescenta uma seção. Parâmetros numéricos citados aqui vivem em
[`vitals_anomaly/config.yaml`](../vitals_anomaly/config.yaml); o texto explica
o porquê de cada valor.

Princípios que valem para todas as etapas:

- **Causalidade.** Em um instante *t* só se usam dados com timestamp ≤ *t*:
  nada de interpolação, normalização ou preenchimento que olhe para o futuro.
- **Sem Azure AI Anomaly Detector.** O serviço foi aposentado em 01/10/2026;
  a detecção é implementada localmente.
- **Parâmetros fora do código.** Limites, janelas e thresholds ficam no config.

---

## Etapa 1 — Ingestão de dados

### 1.1 Organização do código

O repositório já tem pacotes de domínio na raiz (`video/`, `contratos/`, `app/`),
cada um com seus testes em `tests/<pacote>/`, e o `video/pose/` guarda o
`config.yaml` ao lado de um `config.py` que valida o arquivo. A US15 segue o
mesmo padrão:

| Caminho | Conteúdo |
|---|---|
| `vitals_anomaly/schema.py` | schema canônico, `VitalSample` (streaming), validação |
| `vitals_anomaly/config.py` + `config.yaml` | parâmetros da US15 (sem padrões escondidos no código) |
| `vitals_anomaly/data/eicu.py` | adapter do eICU-CRD Demo |
| `vitals_anomaly/data/synthetic.py` | gerador sintético com seed fixa |
| `vitals_anomaly/data/loader.py` | `load_vitals(source=...)`, ponto de entrada único |
| `scripts/download_data.py` | download do eICU com verificação SHA-256 |
| `tests/vitals_anomaly/` | testes pytest |

Os dados baixados ficam em `data/eicu-crd-demo/`, que já está no `.gitignore`.

### 1.2 Schema canônico

Formato **wide**: uma linha por (`patient_id`, `timestamp`) e uma coluna por
sinal. O wide foi escolhido porque os detectores das próximas etapas olham os
sinais do mesmo instante juntos (ex.: NEWS2, correlação FC × PA), e porque é o
formato natural do modo streaming (uma amostra = uma linha).

| Coluna | Tipo (pandas) | Unidade | Obrigatória |
|---|---|---|---|
| `patient_id` | `string` | — | sim |
| `timestamp` | `datetime64[ns, UTC]` | — | sim |
| `hr` | `float64` | bpm | sim (pode ser NaN) |
| `sbp` | `float64` | mmHg | sim (pode ser NaN) |
| `dbp` | `float64` | mmHg | sim (pode ser NaN) |
| `spo2` | `float64` | % (0–100) | sim (pode ser NaN) |
| `rr` | `float64` | irpm | não |
| `temp` | `float64` | °C | não |
| `o2_supplemental` | `boolean` (nulável) | — | não |
| `consciousness` | categoria `A/C/V/P/U` | ACVPU | não |
| `bp_source` | categoria `invasive/noninvasive` | — | não |

Decisões:

- **Timestamps em UTC e com fuso.** Evita ambiguidade em horário de verão e
  na fusão com as outras modalidades, que usam UTC no contrato de eventos.
- **Sinais numéricos em `float64`, faltante = `NaN`.** Todos os sinais existem
  como coluna mesmo quando a fonte não os tem; um sinal que nunca é medido fica
  todo `NaN`. Isso mantém um único schema para todas as fontes.
- **A ingestão não preenche faltantes.** Forward-fill, interpolação ou
  imputação são decisões de modelagem e ficam para a etapa de pré-processamento,
  onde podem ser feitas de forma causal e documentada.
- **`bp_source` foi acrescentado ao schema pedido.** Pressão invasiva (cateter
  arterial) e não invasiva (manguito) diferem sistematicamente; sem essa coluna
  a troca de fonte apareceria como um degrau falso na série.
- **Ordenação e unicidade são garantidas.** `validate_vitals` rejeita
  DataFrames desordenados ou com (`patient_id`, `timestamp`) repetido, e toda
  fonte passa por ele antes de sair de `load_vitals`.
- **`VitalSample` (pydantic) para streaming.** Valida só limites físicos
  (valores não negativos, SpO2 ≤ 100%, timestamp com fuso); faixas clínicas
  são parâmetros de detecção, não de schema. `samples_to_frame` e
  `frame_to_samples` convertem entre os dois modos sem perda.

### 1.3 Fonte real: eICU Collaborative Research Database Demo

| Item | Valor |
|---|---|
| Dataset | eICU Collaborative Research Database Demo, v2.0.1 |
| Origem | PhysioNet — <https://physionet.org/content/eicu-crd-demo/2.0.1/> |
| DOI | [10.13026/4mxk-na84](https://doi.org/10.13026/4mxk-na84) |
| Licença | Open Data Commons Open Database License v1.0 (ODbL); acesso aberto, sem credenciamento |
| Conteúdo | 2.520 internações em UTI (1.841 pacientes) de 20 hospitais dos EUA (2014–2015), subconjunto do eICU-CRD (a página do PhysioNet diz 2.500; a tabela `patient` tem 2.520) |
| Tabelas usadas | `vitalPeriodic`, `vitalAperiodic`, `patient` (CSV gzip) |
| Download | `python scripts/download_data.py` (confere o SHA-256 publicado pelo PhysioNet) |

Citação: Pollard TJ, Johnson AEW, Raffa JD, Celi LA, Mark RG, Badawi O. *The
eICU Collaborative Research Database, a freely available multi-center database
for critical care research.* Scientific Data, 2018. DOI 10.1038/sdata.2018.178.
A ODbL exige atribuição e que bases derivadas redistribuídas mantenham a mesma
licença; por isso os dados não são versionados neste repositório.

Por que o eICU Demo e não o MIMIC-III Waveform Matched Subset: o Demo tem
acesso aberto (o MIMIC exige credenciamento e treinamento CITI para os dados
clínicos), é pequeno (~22 MB), já vem em tabela com os sinais vitais
agregados e tem pressão não invasiva, que é o caso comum fora da UTI.

#### Frequência de amostragem

- `vitalPeriodic`: um registro a cada **5 minutos** por internação. Cada valor
  é a mediana das leituras de 1 minuto do monitor nesse intervalo.
- `vitalAperiodic`: medidas **irregulares** (manguito programado ou manual).
  Medido no Demo: intervalo entre medidas de PA não invasiva com mediana de
  20 min (p10 = 5 min, p25 = 15 min, p75 = 54 min, p90 = 60 min); 241.861
  medidas em 2.329 das 2.520 internações.
- A série canônica do eICU é, portanto, **irregular**: a união dos instantes
  das duas tabelas. A reamostragem (se houver) é decisão da próxima etapa.

#### Cobertura medida após a ingestão (eICU Demo completo)

`load_vitals("eicu")` devolve **2.380 internações** e 1.827.011 linhas. As
outras 140 internações da tabela `patient` não têm nenhum sinal canônico em
`vitalPeriodic`/`vitalAperiodic`.

| Sinal | Internações com o sinal | % das linhas com valor |
|---|---|---|
| FC (`hr`) | 2.364 | 89,1 % |
| PAS / PAD (`sbp`/`dbp`) | 2.347 | 25,3 % |
| SpO2 (`spo2`) | 2.330 | 78,9 % |
| FR (`rr`) | 2.162 | 75,6 % |
| Temperatura (`temp`) | 170 | 6,2 % |

- Intervalo mais comum entre linhas consecutivas: 5 min (1,44 milhão de
  intervalos). Os intervalos de 1 a 4 min vêm das medidas de manguito, que
  caem entre duas linhas do monitor.
- Origem da PA nas linhas que têm PA: 236.608 de manguito e 226.476 invasivas.
- A temperatura é rara no `vitalPeriodic`: no eICU ela fica principalmente em
  `nurseCharting`, fora do escopo desta etapa. A conversão de °F funciona: o
  máximo cru é 103 (°F) e, após a ingestão, 42,9 °C. Restam artefatos (mínimo
  de 0,5 °C), mantidos de propósito para a etapa de limpeza.
- Uma internação sem SpO2, ou sem temperatura, é ausência na fonte e não falha
  da ingestão: a contagem 0 é conferida contra a tabela crua.

#### Verificação no dataset completo

A ingestão foi auditada contra as tabelas cruas em **todas** as internações,
não só em amostras:

```bash
uv run python scripts/auditar_eicu.py      # auditoria impressa, [OK]/[FALHA]
uv run pytest tests/vitals_anomaly -v -rs  # inclui test_eicu_real.py (dataset completo)
```

A auditoria reconstrói do cru, de forma independente, a saída que as regras
desta seção exigem e compara com `load_vitals("eicu")`:

| Verificação | Resultado |
|---|---|
| Internações na saída = internações do cru com algum sinal | 2.380 = 2.380 (as outras 140 da tabela `patient` não têm sinal) |
| Contagem por sinal e por internação (FC, SpO2, FR, temperatura) | iguais ao cru em todas as internações: 1.627.893 / 1.441.699 / 1.381.350 / 112.820 |
| Instantes com PA (invasiva ou manguito) | 463.084 = 463.084 |
| Igualdade valor a valor das 1.827.011 linhas | diferença máxima 0 em todos os sinais; `bp_source` idêntico |
| Temperatura | 112.820 valores em 170 internações, no cru e na saída |
| Conversão °F → °C | 2.912 linhas em °F, em 4 internações (1195714, 1578108, 2689235, 2873463), todas iguais a (F − 32)·5/9; máximo na saída 42,9 °C |
| Schema | `validate_vitals` aceita o dataset completo |

Exemplo de conversão: `eicu_1578108` tem 92,3 °F no cru, que viram 33,5 °C
na saída (hipotermia, possivelmente terapêutica; o valor é mantido).

**Caso `eicu_1034813`.** Essa internação tem 295 temperaturas, mas a primeira
está no offset 2.135 min (2014-01-02 22:50, cerca de 35 h após o início da
série). Por isso as primeiras 200 linhas da série mostram `temp = NaN`. O
`scripts/explorar_eicu.py --patient eicu_1034813` agora imprime também o
bloco "Primeiras linhas com temperatura".

Os testes de integração (`tests/vitals_anomaly/test_eicu_real.py`) repetem
essas comparações e são pulados só quando os dados não foram baixados. Para
confirmar que eles detectam erros, a conversão de °F foi desligada no config
de propósito: o teste de °F falhou e voltou a passar com o config restaurado.

#### Mapeamento de colunas

| eICU (tabela.coluna) | Canônico | Conversão |
|---|---|---|
| `vitalPeriodic.patientunitstayid` | `patient_id` | prefixo `eicu_` (ex.: `eicu_141765`) |
| `observationoffset` + `patient.unitadmittime24` | `timestamp` | ver abaixo |
| `vitalPeriodic.heartrate` | `hr` | nenhuma (bpm) |
| `vitalPeriodic.sao2` | `spo2` | nenhuma (%) |
| `vitalPeriodic.respiration` | `rr` | nenhuma (irpm) |
| `vitalPeriodic.temperature` | `temp` | °F → °C quando o valor está em [86, 113] |
| `vitalPeriodic.systemicsystolic` / `systemicdiastolic` | `sbp` / `dbp` | nenhuma (mmHg); `bp_source = invasive` |
| `vitalAperiodic.noninvasivesystolic` / `noninvasivediastolic` | `sbp` / `dbp` | nenhuma (mmHg); `bp_source = noninvasive` |
| — | `o2_supplemental`, `consciousness` | não disponíveis nessas tabelas: `<NA>` |

Colunas ignoradas: PVC, EtCO2, pressões médias e de artéria pulmonar, ST e PIC
(fora do escopo da US15).

#### Decisões do adapter

- **`patient_id` = internação na UTI (`patientunitstayid`).** As séries do eICU
  são ligadas à internação; um mesmo paciente (`uniquepid`) com duas
  internações tem duas séries independentes, com relógios diferentes.
- **Timestamp sintético que preserva a hora do dia.** O eICU só publica
  offsets em minutos desde a admissão na unidade (datas desidentificadas). O
  timestamp é `anchor_date` (2014-01-01, UTC) + hora de admissão
  (`unitadmittime24`) + offset. A data é fictícia, mas a hora do dia é real,
  o que é necessário para modelar o ritmo circadiano. Internações sem linha na
  tabela `patient` são descartadas com aviso no log (não dá para posicioná-las
  no dia).
- **Offsets negativos são mantidos.** São medidas anteriores à admissão na
  unidade (ex.: no centro cirúrgico) e continuam válidas.
- **Pressão: invasiva tem prioridade, escolhida por par.** No instante em que
  há pressão invasiva, sistólica e diastólica vêm dela; o manguito só é usado
  quando não há invasiva. A escolha é pelo par para nunca misturar a sistólica
  de uma fonte com a diastólica da outra. A origem fica em `bp_source`.
- **Linhas repetidas no mesmo instante viram a média.** Só combina valores do
  mesmo timestamp, então não usa dados futuros.
- **Linhas sem nenhum sinal canônico são descartadas** (ex.: só PVC).
- **Temperatura em °F convertida valor a valor.** Valores em [86, 113] são
  impossíveis em °C para um paciente vivo e típicos em °F (30–45 °C); são
  convertidos. A faixa está no config (`sources.eicu.fahrenheit_range`).
- **Sem limpeza de artefatos na ingestão.** Valores fisiologicamente
  implausíveis (FC 0, SpO2 de sensor solto) são mantidos como vieram; a
  remoção de artefatos é uma decisão de pré-processamento com impacto direto na
  taxa de falsos alarmes, e será feita e documentada na etapa seguinte.
- **A ingestão é causal.** Há um teste que corta as tabelas cruas em um offset
  e confere que nenhuma linha anterior ao corte muda
  (`test_ingestao_e_causal_dados_futuros_nao_alteram_o_passado`).

### 1.4 Fonte sintética

`generate_synthetic_vitals` é o fallback offline e a base dos testes. Cada
sinal de cada paciente é:

```
valor(t) = baseline_paciente
         + A · cos(2π (hora_do_dia(t) − pico) / 24)    # circadiano
         + AR(1)(t)                                     # variação fisiológica lenta
         + ruído branco(t)                              # ruído de medição
```

seguido de corte nos limites físicos e arredondamento à resolução do monitor
(1 bpm, 1 mmHg, 1%, 1 irpm, 0,1 °C).

Decisões:

- **Baseline individual.** Sorteado por paciente de N(média, dp) por sinal, com
  médias de adultos em repouso (FC 78 ± 10 bpm, PA 122/74, SpO2 97%, FR 16,
  T 36,8 °C). É o que torna limiares fixos populacionais insuficientes e
  motiva modelos por paciente nas próximas etapas.
- **Ritmo circadiano leve** com pico às 16h (FC ±5 bpm, PAS ±6 mmHg,
  T ±0,3 °C), coerente com o vale noturno descrito na literatura.
- **Ruído AR(1)** (φ entre 0,8 e 0,97) em vez de só ruído branco, porque sinais
  vitais reais são autocorrelacionados; um detector testado em ruído branco
  seria otimista demais.
- **PAS e PAD acopladas** (correlação 0,6 no baseline e no AR(1)) e PAD sempre
  ao menos 10 mmHg abaixo da PAS.
- **Faltantes MCAR** (2% por sinal) para exercitar o tratamento de faltantes.
- **Reprodutibilidade.** `seed` fixa no config; cada paciente usa um gerador
  derivado da seed pelo seu índice (`SeedSequence.spawn`), então o paciente *i*
  é o mesmo qualquer que seja o número de pacientes gerados.
- **Contexto.** `consciousness = A` para todos; `o2_supplemental` sorteado por
  paciente (20%) e constante na série; `bp_source` nulo.
- **Sem anomalias injetadas** nesta etapa: a série é "normal" por construção.

### 1.5 Pendências registradas

- `o2_supplemental` e `consciousness` no eICU: poderiam vir de
  `respiratoryCharting` / `nurseCharting` (escala de Glasgow → ACVPU), tabelas
  que não estão no escopo desta etapa. Sem elas, o NEWS2 sobre o eICU fica
  incompleto.
- Limpeza de artefatos, tratamento de faltantes e reamostragem: próxima etapa.
- Injeção de anomalias rotuladas no gerador sintético, para avaliação.
