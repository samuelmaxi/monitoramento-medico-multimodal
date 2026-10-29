# Relatório US15 — rastreabilidade ao Definition of Done

Rastreia cada item do *Definition of Done* da US15 até o código, com a evidência e o resultado
medido. O desenho está em [`arquitetura/us15_sinais_vitais.md`](arquitetura/us15_sinais_vitais.md).
As métricas vêm de `saida/sinais/relatorio_us15.json`, gerado por `python scripts/avaliar_us15.py`.

## Como reproduzir

```bash
uv sync
python scripts/baixar_sinais.py          # MIMIC-IV Demo → dados/mimic-iv-demo/
python scripts/avaliar_us15.py           # métricas + rótulos em saida/sinais/
python scripts/rodar_us15.py --velocidade 60 --pacientes 5   # replay "tempo real"
uvicorn app.main:app                     # API: /sinais-vitais/news2 e /sinais-vitais/medicoes
pytest tests/sinais
```

`PSEUDONYM_KEY` precisa estar no `.env` (≥ 32 bytes), como no resto do pipeline.

## Quadro DoD → código

### 1. Séries de FC, PA (sistólica/diastólica) e SpO2 por paciente (FR e temperatura quando disponíveis)

**Status:** ✔. `sinais/mimic.py::carregar` lê os 100 pacientes do MIMIC-IV Demo (77.245
medições) no formato longo `subject_id, instante, sinal, valor`. `DetectorSinais` mantém o
estado separado por paciente pseudonimizado (`pt_<hex>`). FR, temperatura, O2 suplementar e
consciência (GCS ocular como proxy de ACVPU) entram quando existem.

### 2. Limpeza de artefatos e dados faltantes, tratados e documentados

**Status:** ✔. `sinais/limpeza.py`: `FAIXAS` (SpO2 < 50 → descarta, então SpO2 = 0 sai; FC > 300 → descarta)
e `VALIDADE` (forward-fill limitado; depois disso o parâmetro fica ausente e o evento sai com
`news2_parcial`). As regras estão em [`arquitetura/us15_sinais_vitais.md`](arquitetura/us15_sinais_vitais.md#limpeza-de-artefatos).
No MIMIC Demo foram descartados 57 valores (FR 47, FC 3, SpO2 3, Temp 3, PAS 1).
Testes: `tests/sinais/test_detector.py::test_artefato_e_descartado` e `::test_valor_antigo_expira_do_news2`.

### 3. NEWS2 por parâmetro e total, conforme a tabela de referência

**Status:** ✔. `sinais/news2.py` (`TABELA`, `pontos_parametro`, `calcular`). Os pontos de cada
parâmetro e o total vão em `evidence.features` (`news2_fc`, `news2_spo2`, …, `news2_total`).
`tests/sinais/test_news2.py` cobre **os dois lados de cada limite de faixa** da tabela (44 casos).

### 4. Gatilhos NEWS2: total 5–6 ou 3 em parâmetro único = urgente; total ≥ 7 = emergência

**Status:** ✔. `ResultadoNews2.gatilho` gera `news2_resposta_urgente` (`severity=media`) e
`news2_emergencia` (`severity=alta`). O evento sai quando o nível sobe, sem repetir enquanto
o nível se mantém. No replay do MIMIC Demo foram 1.523 eventos urgentes e 1.732 de emergência
(pacientes de UTI).

### 5. Camada de ML não supervisionada sobre a baseline do próprio paciente (mudança súbita e tendência)

**Status:** ✔. `sinais/detector.py::DetectorSinais._baseline`:

- **Mudança súbita** (`desvio_baseline`): z-score móvel robusto (mediana/MAD, 12 h), com
  |z| > 2,5 em 2 medições seguidas e a baseline congelada durante o episódio.
- **Tendência** (`tendencia_deterioracao`): inclinação da regressão linear em 6 h, comparada à
  escala da baseline (limiar 2,5).

Não precisa de treino e aprende com o histórico de cada paciente. Os limiares foram escolhidos
por busca em grade **só no conjunto de ajuste**.

### 6. Janela deslizante / streaming ("tempo real")

**Status:** ✔. `DetectorSinais.processar` recebe uma medição por vez e guarda só `deque`s
deslizantes (12 h / 6 h), a ~0,1 ms por medição. Replay em tempo real em
`scripts/rodar_us15.py --velocidade N`. Streaming pela API em `POST /sinais-vitais/medicoes`.

### 7. Avaliação com anomalias rotuladas: Recall ≥ 0,80 e F1 ≥ 0,70, com matriz de confusão

**Status:** ✔ (**Recall 0,838 · F1 0,756** no conjunto de teste).

**Protocolo** (`sinais/avaliacao.py`): para cada paciente e sinal (FC, PAS, PAD, SpO2, FR), a
série real é cortada em slots de 6 h depois de 12 h de baseline. Metade dos slots, sorteada com
semente 42, recebe uma anomalia de piora clínica, **injetada e marcada**:

- **degrau:** FC +30 bpm, PAS −30, PAD −18, SpO2 −6 %, FR +9 irpm
- **rampa:** de 0 a 2× esse valor ao longo do slot

Os demais slots são negativos. Um slot é predito positivo se o detector emitir `desvio_baseline`
ou `tendencia_deterioracao` para aquele sinal dentro dele. Depois de cada slot positivo vem um
slot de guarda, não pontuado. Os rótulos ficam em `saida/sinais/rotulos_us15.csv` e os eventos
saem com `is_synthetic=true`.

**Separação:** pacientes com `subject_id` par formam o **ajuste** (escolha dos limiares);
os ímpares, o **teste** (número reportado).

**Matriz de confusão: teste (50 pacientes, 2.924 slots)**

|                     | Predito anomalia | Predito normal |
|---|---:|---:|
| **Anomalia injetada** | VP = 1.264 | FN = 244 |
| **Normal**            | FP = 574   | VN = 842 |

| Conjunto | Recall | Precisão | F1 |
|---|---:|---:|---:|
| Ajuste | 0,861 | 0,686 | 0,764 |
| **Teste** | **0,838** | **0,688** | **0,756** |
| Meta | ≥ 0,80 | — | ≥ 0,70 |

Teste por tipo de anomalia: degrau com Recall 0,791 (F1 0,884), rampa com Recall 0,886 (F1 0,940).
Por sinal, o Recall vai de 0,819 (PAD) a 0,864 (FR).

Busca em grade no ajuste (z × consecutivas × tendência; trecho):

| z | k | tendência | Recall | Precisão | F1 |
|---:|---:|---:|---:|---:|---:|
| 3,5 | 2 | 4,0 | 0,594 | 0,836 | 0,695 |
| 2,5 | 2 | 3,0 | 0,809 | 0,742 | 0,774 |
| **2,5** | **2** | **2,5** | **0,845** | **0,694** | **0,762** |
| 2,0 | 2 | 2,0 | 0,872 | 0,614 | 0,721 |

Escolhemos 2,5/2/2,5 em vez do F1 máximo (2,5/2/3,0) porque esse ponto deixa uma folga maior no
Recall, que é a meta mais crítica num monitor clínico.

**Limitações:**

- Os falsos positivos (40 % dos slots negativos) incluem **variações reais** de pacientes de UTI,
  que não têm rótulo. Parte deles provavelmente é deterioração verdadeira, então a precisão
  medida é um piso.
- As anomalias são sintéticas, com magnitude fixa. Os degraus no limite da variabilidade do
  paciente são os mais perdidos.

### 8. Não utiliza Azure AI Anomaly Detector

**Status:** ✔. A detecção é toda local (numpy/pandas) e o SDK não está em `pyproject.toml`.

### 9. API

**Status:** ✔. `app/routers/sinais_vitais.py`, registrado em `app/main.py`:

- `POST /sinais-vitais/news2`: recebe `{fc, pas, spo2, fr, temp, o2_suplementar, alerta}` e
  devolve os pontos por parâmetro, o total, o gatilho, os ausentes e os artefatos descartados.
- `POST /sinais-vitais/medicoes`: recebe um lote `{patient_id, timestamp, sinal, valor}`. O
  `patient_id` é pseudonimizado na entrada. Devolve os `EventoAchado` gerados, e a baseline se
  mantém entre chamadas.

Testes: `tests/sinais/test_api_sinais.py`.
