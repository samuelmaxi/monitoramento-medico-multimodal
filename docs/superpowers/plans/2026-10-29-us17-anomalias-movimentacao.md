# US17 · Anomalias nos padrões de movimentação durante a internação — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** gerar, por paciente, uma série de movimentação (posição, saídas do leito, quedas, dia/noite) a partir da pose (US06), das áreas (US07) e dos desvios (US08), calcular métricas e emitir `EventoAchado` da modalidade `movimentacao` para imobilidade > 2 h, saída do leito/queda (alta à noite) e desvio da baseline individual, com Recall ≥ 0,80 em quedas.

**Architecture:** pacote novo `movimentacao/` no mesmo molde de `video/desvios/` (US08) e `prescricoes/` (US16): *adaptadores* transformam cada fonte numa lista de `Observacao` → `serie.py` deriva marcos e métricas → `regras.py` aplica limiares de `config/us17_movimentacao.yaml` → `eventos.py` traduz para o contrato da US04 → `run.py` grava JSONL + relatório. A US17 **não chama** US06/07/08: lê o que elas gravam (pose JSON, JSONL de eventos), como a US08 faz. Duas fontes de série:
1. **Vídeo** (US06/07/08): fonte-alvo do sistema; clipes curtos → serve para **quedas e saídas do leito**.
2. **MIMIC-IV Demo `chartevents`** (já em `dados/mimic-iv-demo/`): registros reais de enfermagem `Position` (224093), `Turn` (224082), `Activity` (224084) cobrindo dias de internação → serve para **imobilidade, reposicionamentos/hora e baseline**, que um clipe de segundos não consegue mostrar.

**Tech Stack:** Python 3.13, pandas, numpy, pyyaml, pydantic (contratos), ultralytics (YOLOv8, já instalado), pytest.

**Spec:** a task US17 (DoD abaixo) + `contratos/catalogo.py` (event_types da modalidade `movimentacao` já existem) + `contratos/exemplos/movimentacao_imobilidade_prolongada.json` (formato esperado do evento).

## Global Constraints

- Modalidade `movimentacao`; `event_type` ∈ {`imobilidade_prolongada`, `saida_do_leito`, `queda`, `agitacao_subita`, `reducao_mobilidade`, `sem_achados`} — **nenhuma mudança no schema**.
- `model_version = "anomalias-movimentacao@0.1.0"` (o mesmo do exemplo do contrato).
- Imobilidade: limiar padrão **120 min** (NPIAP/EPUAP/PPPIA 2019), **configurável** no YAML.
- Queda → severidade `alta` sempre; saída do leito no período **noturno** → `alta`.
- Nenhum limiar no código: tudo em `config/us17_movimentacao.yaml` com `versao`, que vai em `evidence.thresholds.versao_limiares` de cada evento (padrão US08).
- `patient_id`/`encounter_id` sempre pseudonimizados (`contratos.pseudonimizar_paciente/_internacao`); nada de `subject_id` cru no evento.
- Sem dependência nova. Código e nomes em português, como o resto do repositório. `ruff` limpo (linha ≤ 100).
- Testes em `tests/movimentacao/`; `pytest` roda da raiz.
- Recall ≥ 0,80 em quedas, medido em partição de teste não usada para calibrar.

## Review Focus

1. **Série com buracos** (câmera sem pessoa, enfermagem sem registro por 4 h): lacuna maior que `lacuna_max_min` não pode virar "imobilidade" — vira trecho `desconhecido` e o evento não dispara. Teste em Task 2.
2. **Cama rotativa** (`Rotating` no MIMIC) e posição `desconhecida`: não contam como mudança nem zeram o relógio de imobilidade. Teste em Task 2/4.
3. **Noite que cruza a meia-noite** (22:00–06:00) e fuso: `periodo()` precisa acertar 23:30 e 05:59 como noite, 06:00 como dia. Teste em Task 2.
4. **Baseline curta** (paciente recém-admitido, < `baseline_min_h` de histórico): não emite agitação/redução. Teste em Task 3.
5. **Mesma queda vista por duas fontes** (pose US08 e caixa YOLO): um evento só, com as duas evidências. Teste em Task 5.

---

## Estrutura de arquivos

| Arquivo | Responsabilidade |
| --- | --- |
| `movimentacao/__init__.py` | `MODEL_VERSION` |
| `movimentacao/modelos.py` | `Posicao`, `Observacao`, `Marco`, `Anomalia` (dataclasses) |
| `movimentacao/config.py` + `config/us17_movimentacao.yaml` | carga/validação dos limiares |
| `movimentacao/serie.py` | marcos (mudança de posição, saída/retorno ao leito, queda), período dia/noite, métricas |
| `movimentacao/regras.py` | detectores → `list[Anomalia]` |
| `movimentacao/mimic.py` | `chartevents` → `Observacao` por `stay_id` |
| `movimentacao/video.py` | pose (US06) + eventos US07/US08 + caixa `person` → `Observacao` |
| `movimentacao/eventos.py` | `Anomalia` → `EventoAchado` |
| `movimentacao/injecao.py` + `avaliacao.py` | casos rotulados sintéticos sobre séries reais + recall/FP |
| `movimentacao/run.py` | CLI |
| `scripts/avaliar_us17.py` | avaliação (vídeo rotulado + injeção) → `saida/movimentacao/avaliacao_us17.json` |
| `data/eval/us17/quedas.csv` | rótulos dos clipes: `video_id,queda,instante_s,particao` |
| `docs/relatorio_us17_dod.md` | rastreabilidade DoD → código → números |

---

### Task 1: Modelos e configuração

**Files:**
- Create: `movimentacao/__init__.py`, `movimentacao/modelos.py`, `movimentacao/config.py`, `config/us17_movimentacao.yaml`
- Test: `tests/movimentacao/__init__.py`, `tests/movimentacao/test_config.py`

**Interfaces — Produces:**
- `MODEL_VERSION = "anomalias-movimentacao@0.1.0"`
- `class Posicao(StrEnum)`: `SUPINO, LATERAL_ESQ, LATERAL_DIR, PRONO, SENTADO, ROTATIVO, FORA_DO_LEITO, CHAO, DESCONHECIDA`
- `@dataclass(frozen=True) Observacao(instante: datetime, posicao: Posicao, fonte: str, queda: bool = False, mudanca: bool = False, evidencia: dict = {})` — `mudanca=True` = reposicionamento registrado sem posição nova (ex.: `Turn: Side to Side`).
- `@dataclass(frozen=True) Marco(tipo: Literal["mudanca_posicao","saida_leito","retorno_leito","queda"], instante: datetime, periodo: Literal["dia","noite"], de: Posicao, para: Posicao, fonte: str)`
- `@dataclass(frozen=True) Anomalia(event_type: str, inicio: datetime, fim: datetime, severidade: Severidade, score: float, resumo: str, features: dict, fontes: tuple[str, ...])`
- `@dataclass(frozen=True) ConfigMovimentacao` com campos abaixo; `carregar_config(caminho=CAMINHO_PADRAO) -> ConfigMovimentacao`, `config_de_dict(d) -> ConfigMovimentacao`, `ErroDeConfiguracao(ValueError)`.

YAML (valores do DoD fixados aqui):

```yaml
versao: "1.0.0"
fuso: "UTC"                 # MIMIC já vem deslocado em UTC; câmera real: America/Sao_Paulo
noite: {inicio: "22:00", fim: "06:00"}
lacuna_max_min: 150         # sem observação além disso → trecho desconhecido
imobilidade:
  limiar_min: 120           # NPIAP/EPUAP/PPPIA 2019: reposicionar a cada ~2 h
  severidade: media
  severidade_acima_de_min: {240: alta}
saida_leito: {severidade_dia: media, severidade_noite: alta, persistencia_s: 3}
queda: {severidade: alta, fusao_janela_s: 5}
baseline:
  janela_h: 24              # histórico do próprio paciente
  janela_atual_h: 6
  baseline_min_h: 12
  agitacao_fator: 2.0       # taxa atual ≥ 2× a mediana da baseline
  reducao_fator: 0.5        # taxa atual ≤ 0,5× a mediana
  minimo_eventos_baseline: 3
  severidade: media
score: {saturacao_imobilidade_min: 240}
```

- [ ] **Step 1:** testes em `test_config.py`:
  - `test_carrega_padrao`: `cfg.imobilidade.limiar_min == 120`, `cfg.saida_leito.severidade_noite is Severidade.ALTA`, `cfg.versao == "1.0.0"`.
  - `test_limiar_configuravel`: `config_de_dict({... imobilidade.limiar_min: 90 ...}).imobilidade.limiar_min == 90`.
  - `test_recusa_chave_desconhecida`, `test_recusa_severidade_fora_do_catalogo`, `test_recusa_noite_mal_formatada` → `pytest.raises(ErroDeConfiguracao)`.
- [ ] **Step 2:** `pytest tests/movimentacao/test_config.py -v` → FAIL (módulo inexistente).
- [ ] **Step 3:** implementar; reaproveitar o estilo de validação de `video/desvios/config.py` (`_get`, `_sem_chaves_extras`, `_severidade`) — copiar o padrão, não importar os privados.
- [ ] **Step 4:** testes passam.
- [ ] **Step 5:** commit `feat(us17): movement models and versioned thresholds`.

### Task 2: Série de movimentação — marcos, período e métricas

**Files:**
- Create: `movimentacao/serie.py`
- Test: `tests/movimentacao/test_serie.py`, `tests/movimentacao/conftest.py` (fábrica `obs(hhmm, posicao, **kw)` em 2026-10-01 UTC)

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `periodo(instante: datetime, cfg) -> Literal["dia","noite"]`
  - `marcos(observacoes: Sequence[Observacao], cfg) -> list[Marco]` — ordena por instante; `DESCONHECIDA`/`ROTATIVO` não geram mudança; entrada em `FORA_DO_LEITO` = `saida_leito` (somente se persistir `persistencia_s`); `CHAO` ou `queda=True` = `queda`; `mudanca=True` = `mudanca_posicao` com `de == para`.
  - `trechos_sem_mudanca(observacoes, marcos, cfg) -> list[tuple[datetime, datetime, bool]]` — `(inicio, fim, confiavel)`; trecho com lacuna > `lacuna_max_min` → `confiavel=False`; trecho em `ROTATIVO`/`FORA_DO_LEITO` não entra.
  - `metricas(observacoes, cfg, ate: datetime | None = None) -> dict[str, float | int]` com chaves exatas: `minutos_sem_mudanca` (trecho atual), `maior_minutos_sem_mudanca`, `reposicionamentos_por_hora`, `saidas_do_leito`, `saidas_do_leito_noite`, `quedas`, `horas_observadas`.

- [ ] **Step 1:** testes:
  - `test_noite_cruza_meia_noite`: 23:30 e 05:59 → `"noite"`; 06:00 e 21:59 → `"dia"`.
  - `test_mudanca_de_posicao`: SUPINO 08:00 → LATERAL_ESQ 10:00 → LATERAL_ESQ 11:00 → 1 marco `mudanca_posicao` às 10:00.
  - `test_turn_sem_posicao_conta`: `obs("10:00", SUPINO, mudanca=True)` após SUPINO 08:00 → 1 marco.
  - `test_saida_do_leito_noturna`: FORA_DO_LEITO 23:10 → marco `saida_leito`, `periodo == "noite"`; volta SUPINO 23:30 → `retorno_leito`.
  - `test_queda`: CHAO 03:00 → marco `queda`.
  - `test_rotativo_e_desconhecida_nao_mudam`: SUPINO→ROTATIVO→DESCONHECIDA→SUPINO → 0 marcos de mudança.
  - `test_lacuna_quebra_trecho`: SUPINO 08:00 e SUPINO 12:00 sem nada no meio (lacuna 240 > 150) → `trechos_sem_mudanca` tem `confiavel=False`.
  - `test_metricas`: série de 6 h com 3 mudanças e 1 saída → `reposicionamentos_por_hora == 0.5`, `saidas_do_leito == 1`, `maior_minutos_sem_mudanca == <valor do fixture>`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** implementar com `zoneinfo.ZoneInfo(cfg.fuso)` para o período; nada de pandas aqui (listas pequenas).
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(us17): movement series, day/night and metrics`.

### Task 3: Regras de detecção

**Files:**
- Create: `movimentacao/regras.py`
- Test: `tests/movimentacao/test_regras.py`

**Interfaces:**
- Consumes: `marcos`, `trechos_sem_mudanca`, `metricas`, `periodo` (Task 2).
- Produces: `detectar(observacoes: Sequence[Observacao], cfg, *, tipos: Iterable[str] | None = None) -> list[Anomalia]` — ordenadas por `inicio`; `features` sempre inclui as métricas do instante do achado.

Regras:
- `imobilidade_prolongada`: cada trecho `confiavel` com duração > `limiar_min` → 1 anomalia (`inicio` = início do trecho, `fim` = instante em que passou o limiar ou fim do trecho); severidade escalona por `severidade_acima_de_min`; `score = 0.5 + 0.5·min(1, excesso/(saturacao − limiar))`.
- `saida_do_leito`: cada marco `saida_leito`; severidade por período.
- `queda`: cada marco `queda`, `alta`, `score = 1.0`; marcos de queda de fontes distintas a ≤ `fusao_janela_s` viram uma anomalia com `fontes` unidas.
- `agitacao_subita` / `reducao_mobilidade`: taxa de marcos (mudanças + saídas) por hora na `janela_atual_h` comparada à mediana das taxas horárias das `janela_h` anteriores; só avalia se há ≥ `baseline_min_h` de histórico e ≥ `minimo_eventos_baseline` marcos nele. Uma anomalia por janela contígua acima/abaixo do fator (não uma por hora). `features` traz `taxa_atual`, `taxa_baseline`, `razao`.

- [ ] **Step 1:** testes:
  - `test_imobilidade_acima_de_2h`: SUPINO de 08:00 a 10:30 com observações a cada 30 min → 1 `imobilidade_prolongada`, `features["minutos_sem_mudanca"] >= 120`, `severidade == MEDIA`.
  - `test_imobilidade_exatamente_2h_nao_dispara` (limiar estrito `>`).
  - `test_imobilidade_4h_e_alta`.
  - `test_limiar_configuravel_90`: mesma série de 100 min com `limiar_min=90` → dispara; com 120 → não.
  - `test_lacuna_nao_vira_imobilidade` (Review Focus 1).
  - `test_saida_noturna_alta` / `test_saida_diurna_media`.
  - `test_queda_sempre_alta` (às 14:00).
  - `test_queda_duas_fontes_um_evento` (Review Focus 5): CHAO via `video_pose` 03:00:01 e via `video_caixa` 03:00:03 → 1 anomalia, `fontes == ("video_caixa","video_pose")`.
  - `test_agitacao_subita`: 24 h com 1 mudança/2 h, depois 6 h com 3/h → 1 `agitacao_subita`.
  - `test_reducao_mobilidade`: 24 h com 1/h, depois 6 h sem nenhuma (mas observado) → 1 `reducao_mobilidade`.
  - `test_baseline_curta_nao_emite` (Review Focus 4): 8 h de histórico → nenhuma.
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(us17): immobility, bed exit, fall and baseline-deviation rules`.

### Task 4: Fonte MIMIC-IV Demo (`chartevents`)

**Files:**
- Create: `movimentacao/mimic.py`
- Modify: `.gitignore` (adicionar `dados/`)
- Test: `tests/movimentacao/test_mimic.py` (DataFrame em memória; nada lido do disco)

**Interfaces — Produces:**
- `ITENS = {224093: "Position", 224082: "Turn", 224084: "Activity"}`
- `observacoes_de_dataframe(df: pd.DataFrame) -> dict[tuple[str, str, str], list[Observacao]]` — chave `(subject_id, hadm_id, stay_id)`; `instante = charttime` em UTC (datas deslocadas do MIMIC ficam como estão, como a US16).
- `carregar(diretorio: Path = Path("dados/mimic-iv-demo")) -> dict[...]` — lê só as colunas e `itemid` necessários (`usecols`, filtro em chunks) do `chartevents.csv.gz`.

Mapeamento (`fonte="mimic_<item>"`):

| item / valor | Observacao |
| --- | --- |
| Position `Supine` / `Left Side` / `Right Side` / `Prone` / `Sitting` / `Rotating` / `Reverse T-berg` | `SUPINO` / `LATERAL_ESQ` / `LATERAL_DIR` / `PRONO` / `SENTADO` / `ROTATIVO` / `SUPINO` |
| Turn `Left Side` / `Right Side` / `Back` / `Prone` | posição correspondente (`Back` = `SUPINO`) |
| Turn `Side to Side`, `Turns by Self`, `Logroll*`, `Per hospital policy` | `DESCONHECIDA` com `mudanca=True` |
| Activity `Chair`, `Commode`, `Ambulate`, `Bed as Chair` | `FORA_DO_LEITO` (exceto `Bed as Chair` → `SENTADO`) |
| Activity `Dangle` | `SENTADO` |
| Activity `Bedrest` e valores não mapeados | ignorados (contados em `ignorados` do relatório) |

- [ ] **Step 1:** testes: `test_mapeia_position`, `test_turn_side_to_side_e_mudanca`, `test_activity_chair_e_fora_do_leito`, `test_valor_desconhecido_ignorado`, `test_agrupa_por_stay`, `test_ordena_por_charttime`.
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** PASS.
- [ ] **Step 5:** smoke manual: `python -c "from movimentacao.mimic import carregar; d=carregar(); print(len(d), max(map(len,d.values())))"` → ~140 stays, maior série com centenas de observações.
- [ ] **Step 6:** commit `feat(us17): MIMIC-IV demo nursing position/turn/activity as movement series`.

### Task 5: Fonte vídeo (US06 pose + US07 áreas + US08 quedas + caixa `person`)

**Files:**
- Create: `movimentacao/video.py`
- Test: `tests/movimentacao/test_video.py`

**Interfaces:**
- Consumes: `video.pose.sequence.PoseSequence` / `load` (US06), `video.pose.angles.inclination_from_vertical`, `hip_center`; eventos US07/US08 via `video.desvios.ler_eventos_jsonl`; `video.detector.DetectorYolo` (só na avaliação).
- Produces:
  - `posicao_de_keypoints(kp: dict[str, Keypoint], cfg) -> Posicao` — tronco (pescoço→quadril) com inclinação < `video.inclinacao_sentado_max` (40°) → `SENTADO`; > `video.inclinacao_deitado_min` (60°) → deitado; deitado com largura de ombros / comprimento do tronco < `video.razao_lateral_max` (0.35) → `LATERAL_ESQ/DIR` (lado pelo nariz em relação ao meio dos ombros), senão `SUPINO`; esqueleto incompleto → `DESCONHECIDA`.
  - `observacoes_de_pose(seq: PoseSequence, inicio_utc: datetime, cfg) -> list[Observacao]` — mediana por janela de `video.janela_s` (1 s) para não oscilar quadro a quadro.
  - `observacoes_de_areas(eventos: Iterable[EventoAchado], area_leito: str, inicio_utc) -> list[Observacao]` — `entrada_area_critica` de `person` na ROI fora do leito (`lateral_leito` em `config/leito_uti_07.json`) → `FORA_DO_LEITO`; `saida_area_critica` → `DESCONHECIDA` (voltou ao campo do leito).
  - `observacoes_de_quedas_us08(eventos) -> list[Observacao]` — `mudanca_brusca_ou_queda` cujo `features` indica queda → `CHAO, queda=True, fonte="video_pose"`.
  - `quedas_por_caixa(caixas: Sequence[tuple[float, float, float, float, float]], cfg) -> list[float]` — entrada `(t_s, x1, y1, x2, y2)` da maior caixa `person` por quadro; queda = altura cai ≥ `video.queda_caixa.queda_altura_frac` (0.4) em ≤ `tempo_max_s` (1.0) **e** razão largura/altura ≥ `razao_deitado` (1.0) mantida por `janela_s` (0.5); retorna instantes. É o "segundo sinal" que o relatório da US08 pede (queda só por pose: recall 0,23).
- Limiares acima entram no YAML (bloco `video:`), versão sobe para `1.1.0`.

- [ ] **Step 1:** testes com keypoints sintéticos (sem OpenPose):
  - `test_em_pe_ou_sentado` (tronco vertical → `SENTADO`), `test_deitado_supino`, `test_deitado_lateral_esq`, `test_esqueleto_incompleto_desconhecida`.
  - `test_mediana_por_janela_ignora_quadro_isolado`.
  - `test_area_lateral_vira_fora_do_leito` (usar 2 linhas reais de `saida/video/eventos_us07.jsonl` como fixture).
  - `test_queda_por_caixa_detecta`: caixa 100×300 → 300×110 em 0,6 s e fica 1 s → 1 instante.
  - `test_deitar_devagar_nao_e_queda`: mesma mudança em 4 s → nenhuma.
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(us17): video adapters (pose posture, bed ROI, US08 falls, person-box fall)`.

### Task 6: Tradução para o contrato e CLI

**Files:**
- Create: `movimentacao/eventos.py`, `movimentacao/run.py`
- Test: `tests/movimentacao/test_eventos.py`, `tests/movimentacao/test_run.py`

**Interfaces:**
- Produces:
  - `montar_evento(anomalia: Anomalia, cfg, *, paciente: str, internacao: str | None, contexto: dict | None = None, chave=None, sintetico=False) -> EventoAchado` — `modality=MOVIMENTACAO`, `timestamp = anomalia.fim` (instante em que virou achado; ver exemplo do contrato), `window = (inicio, fim)`, `evidence.summary = resumo` (ex.: `"2 h 40 min sem mudança de posição no leito"`), `evidence.features = features`, `evidence.thresholds = {…limiares usados…, "versao_limiares": cfg.versao}`, pseudonimização igual a `prescricoes/eventos.py::_paciente/_contexto` (US16).
  - `montar_sem_achados(paciente, internacao, janela, metricas, cfg) -> EventoAchado`.
  - `relatorio(observacoes, anomalias, cfg) -> dict` — métricas da Task 2, contagem por `event_type`, `fontes`, `ignorados`.
  - CLI: `python -m movimentacao.run mimic [--stay-id ID] [--saida saida/movimentacao]` e `python -m movimentacao.run video --pose P.json [--areas A.jsonl] [--desvios D.jsonl] --inicio ISO --paciente ID [--saida …]` → `<id>_eventos.jsonl` (via `contratos.EmissorJsonl`) + `<id>_relatorio.json`.

- [ ] **Step 1:** testes:
  - `test_evento_valido_no_schema`: `contratos.testing.assert_eventos_validos([montar_evento(...)])`.
  - `test_formato_igual_ao_exemplo`: imobilidade de 160 min → `features["minutos_sem_mudanca"] == 160`, `thresholds["limiar_min"] == 120`, `model_version == MODEL_VERSION`.
  - `test_patient_id_pseudonimizado`: `"10005817"` → começa com `pt_`.
  - `test_sem_achados_quando_nada_dispara`.
  - `test_run_mimic_grava_jsonl` (`tmp_path`, DataFrame mínimo via monkeypatch de `carregar`).
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** PASS.
- [ ] **Step 5:** rodar no demo inteiro: `python -m movimentacao.run mimic` → JSONL válido (`python -m contratos.validar saida/movimentacao/*_eventos.jsonl`).
- [ ] **Step 6:** commit `feat(us17): emit movement findings on the US04 contract + CLI`.

### Task 7: Avaliação em sequências rotuladas (Recall ≥ 0,80 em quedas)

**Files:**
- Create: `movimentacao/injecao.py`, `movimentacao/avaliacao.py`, `scripts/avaliar_us17.py`, `data/eval/us17/quedas.csv`
- Test: `tests/movimentacao/test_avaliacao.py`

**Interfaces:**
- `@dataclass Caso(id, event_type, positivo: bool, instante: datetime, observacoes: list[Observacao], descricao)`
- `gerar_casos(series: Mapping[..., list[Observacao]], cfg, *, seed: int, n_por_tipo: int) -> list[Caso]` — sobre séries **reais** do MIMIC, injeta: imobilidade (remove mudanças por 130–240 min), queda (CHAO dia/noite), saída noturna, agitação (3×/h por 6 h), redução (6 h sem mudança após baseline ativa); controles: imobilidade de 100 min, saída diurna (esperado `media`, não `alta`), deitar devagar.
- `avaliar(casos, cfg) -> dict[str, Contagem]` — mesmo `Contagem` (recall, taxa de FP, perdidos) da US16 `prescricoes/avaliacao.py`; acerto = anomalia do tipo a ≤ 5 min do instante injetado **e** severidade esperada.
- `scripts/avaliar_us17.py --pose-dir outputs/pose [--seeds 5] [--n 100]`:
  1. **Vídeo (quedas reais):** para cada clipe de `data/eval/us17/quedas.csv` com `particao=teste`, junta `observacoes_de_quedas_us08` (pose já extraída pela US06) + `quedas_por_caixa` (YOLO `person` por quadro) e conta recall/precisão por clipe. Calibração só na `particao=calibracao` (mesma regra MD5-paridade da US08 em `scripts/avaliar_us08.py`).
  2. **Injeção (todos os tipos):** 5 seeds × 100 casos por tipo.
  3. Grava `saida/movimentacao/avaliacao_us17.json`; sai com código 1 se recall de `queda` (vídeo) < 0,80.

- [ ] **Step 1:** testes: `test_caso_de_queda_injetado_e_detectado`, `test_controle_100min_nao_dispara`, `test_saida_diurna_nao_conta_como_alta`, `test_contagem_recall` (2 de 3 → 0,667).
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** PASS.
- [ ] **Step 5:** preencher `data/eval/us17/quedas.csv` com os clipes de `conteudos/videos/` (`B_D_*`, `B_N_*`) a partir dos rótulos do FallVision — **confirmar com o grupo o significado de `B_D`/`B_N`** (o relatório da US08 mostra queda em `B_N_03`, então `N` não é "no fall").
- [ ] **Step 6:** extrair pose dos clipes que faltam (`python -m video.pose.run …`) e rodar `python scripts/avaliar_us17.py`. Se recall de queda < 0,80, calibrar **só** `video.queda_caixa` na partição de calibração, subir `versao` e rodar de novo.
- [ ] **Step 7:** commit `feat(us17): labeled evaluation (fall recall on video + injected cases)` com o JSON de avaliação.

### Task 8: Relatório DoD e documentação

**Files:**
- Create: `docs/relatorio_us17_dod.md`
- Modify: `README.md` (seção US17: como rodar), `docs/arquitetura/README.md` (linha da camada 3 aponta `movimentacao/`)

- [ ] **Step 1:** relatório no formato de `docs/relatorio_us08_dod.md`: fluxo, quadro DoD → código → status, números da Task 7 (recall de queda em vídeo com IC, recall por tipo na injeção, FP dos controles), 1 achado real do MIMIC documentado (stay, janela, minutos sem mudança), decisões (MIMIC como fonte de série longa; lacuna ≠ imobilidade; queda com dois sinais) e limitações (registro de enfermagem não é sensor: ausência de registro não prova imobilidade; MIMIC não tem quedas rotuladas; período noturno depende do fuso configurado).
- [ ] **Step 2:** `pytest -q && ruff check movimentacao tests/movimentacao scripts/avaliar_us17.py` → tudo verde.
- [ ] **Step 3:** commit `docs(us17): DoD report and usage`.

---

## Mapa DoD → Task

| DoD | Task |
| --- | --- |
| Série por paciente a partir de pose (US06) e objetos/áreas (US07): posição, saídas, dia/noite | 2, 4, 5 |
| Métricas: tempo sem mudança, reposicionamentos/h, nº de saídas | 2 (`metricas`), 6 (relatório) |
| Alerta de imobilidade > 2 h, limiar configurável | 1 (YAML), 3 |
| Queda / saída noturna = severidade alta | 3 |
| Desvio da baseline individual (agitação, redução) | 3 |
| Recall ≥ 0,80 para quedas em sequências rotuladas | 5 (`quedas_por_caixa`), 7 |

## Fora do escopo (deliberado)

- Rota FastAPI/Postman (`/movimentacao/...`): o DoD não pede; a US16 fez, dá para seguir o mesmo molde depois.
- Baseline com ML (Isolation Forest etc.): a razão contra a mediana do próprio paciente cobre o DoD e é explicável no evento.
- Rastrear vários pacientes no mesmo quadro: 1 câmera = 1 leito = 1 paciente (`bed_id`).
