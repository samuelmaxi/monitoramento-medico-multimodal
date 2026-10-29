# Relatório US17 — rastreabilidade ao Definition of Done

**US17 · Detectar anomalias nos padrões de movimentação durante a internação.**
O objetivo é detectar anomalias nos padrões de movimentação do paciente
internado. Cada item do DoD abaixo aponta para o código, a configuração, os
testes e o *status* real.
Referência clínica: NPIAP/EPUAP/PPPIA, *Prevention and Treatment of Pressure
Ulcers/Injuries: Clinical Practice Guideline* (reposicionamento de referência
a cada ~2 h).

Os caminhos são relativos à raiz do repositório. Os números vêm de
[`saida/movimentacao/avaliacao_us17.json`](../saida/movimentacao/avaliacao_us17.json),
gerado por `uv run python scripts/avaliar_us17.py` com os limiares versão
**1.3.0** de [`config/us17_movimentacao.yaml`](../config/us17_movimentacao.yaml).

## Resumo

| # | Item do DoD | Status |
| --- | --- | --- |
| 1 | Série de movimentação por paciente a partir da pose (US06) e objetos/áreas (US07): mudanças de posição, saídas do leito, dia/noite | ✔ implementado · ◐ a postura pela pose da US06 não foi rodada em vídeo real (ver item 1) |
| 2 | Métricas: tempo sem mudança de posição, reposicionamentos/h, nº de saídas do leito | ✔ |
| 3 | Alerta de imobilidade > 2 h (NPIAP/EPUAP), limiar configurável | ✔ |
| 4 | Queda ou saída do leito à noite com severidade alta | ✔ |
| 5 | Desvio da baseline individual (agitação súbita, redução brusca) | ✔ |
| 6 | Recall ≥ 0,80 para quedas em sequências rotuladas | ✔ **0,875** (14/16 clipes) na partição de teste · ◐ amostra pequena e rótulos ainda sem conferência humana (ver item 6) |

## Fluxo

```text
vídeo de leito ─► US06 pose (JSON) ─────────► postura por janela de 1 s ──┐
               ├► US07 áreas (JSONL) ───────► pessoa na ROI fora do leito ┤
               ├► US08 desvios (JSONL) ─────► queda pela pose ────────────┤
               └► YOLOv8 da US07 (caixa) ───► queda pela descida da caixa ┤
MIMIC-IV Demo chartevents (Position/Turn/Activity) ─► posição registrada ─┤
                                                                          ▼
                 movimentacao/serie.py ─► marcos (mudança, saída, retorno, queda),
                                          período dia/noite, trechos sem mudança, métricas
                                                                          ▼
                 movimentacao/regras.py + config/us17_movimentacao.yaml (versionado)
                                                                          ▼
                 movimentacao/eventos.py ─► EventoAchado (US04), modalidade "movimentacao"
                                          ─► fusão (US18) / alertas (US19)
```

A US17 não chama a US06, a US07 nem a US08: lê o que elas gravam, como a US08
faz com a US06 e a US07. O detector YOLO é o da US07
(`video/detector.py`), instanciado com parâmetros próprios.

**Por que duas fontes.** Imobilidade > 2 h, reposicionamentos por hora e
baseline de 24 h pedem séries de horas a dias. Os vídeos disponíveis têm de 1 a
6 s. O registro de enfermagem do MIMIC-IV Demo (`dados/mimic-iv-demo/`, 139
estadias de UTI, 488 paciente-dias) tem posição (`Position`, item 224093),
reposicionamento (`Turn`, 224082) e atividade (`Activity`, 224084) ao longo de
toda a estadia, então é ele que exercita essas regras com dados reais. O vídeo
exercita queda e saída do leito. As duas fontes geram a mesma `Observacao`, e
as regras não sabem de onde ela veio.

## Quadro DoD → código

### 1. Série de movimentação por paciente

**Status:** ✔ implementado · ◐ validação parcial na fonte de pose.

| Fonte | Código | O que vira observação | Validado com |
| --- | --- | --- | --- |
| pose US06 | `movimentacao/video.py::observacoes_de_pose`, `posicao_de_keypoints` | posição (sentado, supino, lateral esq./dir.) por janela de 1 s, a mais frequente | keypoints sintéticos (`tests/movimentacao/test_video.py`) |
| áreas US07 | `observacoes_de_areas` | `person` entrando na ROI `lateral_leito` = fora do leito | linhas reais de `saida/video/eventos_us07.jsonl` |
| desvios US08 | `observacoes_de_quedas_us08` | `mudanca_brusca_ou_queda` com regra `queda` = chão | evento sintético no formato da US08 |
| caixa YOLOv8 | `quedas_por_descida`, `quedas_por_caixa` | queda | 40 clipes reais (item 6) |
| enfermagem MIMIC | `movimentacao/mimic.py` | posição, reposicionamento, fora do leito (`Chair`, `Commode`, `Ambulate`) | 139 estadias reais |

- **Marcos** (`movimentacao/serie.py::marcos`): mudança de posição, saída do
  leito, retorno ao leito e queda, cada um marcado com o período (`dia` ou
  `noite`, janela 22:00–06:00 configurável, com fuso IANA configurável).
  Posição `desconhecida` não apaga a última conhecida. Na pose, uma posição
  nova só vale se durar `video.persistencia_s` (10 s). Esqueleto que oscila
  entre supino e lateral por alguns quadros não vira reposicionamento
  (`test_oscilacao_curta_da_pose_nao_e_reposicionamento`). Cama terapêutica em
  rotação (`Rotating`) não conta como reposicionamento e não acumula
  imobilidade.
- **Uma troca, um marco:** o MIMIC registra `Position` e `Turn` no mesmo
  instante para a mesma troca. Isso contava dois reposicionamentos. Foi
  corrigido e tem teste
  (`test_mesma_troca_registrada_por_duas_fontes_conta_uma_vez`).
- **Execução:** `python -m movimentacao.run mimic` gera 861 eventos válidos no
  contrato a partir das 139 estadias. `python -m movimentacao.run video
  --video conteudos/videos/B_D_0002.mp4 ...` gera uma `queda` alta no clipe real.

**Lacuna — postura pela pose.** Os pesos do OpenPose não estão no repositório e
nenhuma pose da US06 foi gravada (`outputs/pose/` não existe). Por isso a
classificação de postura só foi testada com keypoints sintéticos. Ela também
depende da posição da câmera: com câmera lateral, os ombros de um paciente em
supino se sobrepõem na imagem e ele pode virar "lateral". O limiar
`video.razao_lateral_max` precisa ser calibrado com a câmera real. A saída do
leito pela US07 também depende de uma ROI desenhada para cada câmera. No clipe
`B_D_0002`, a ROI veio do template de outra câmera e não gerou nenhuma entrada.

### 2. Métricas

**Status:** ✔ — `movimentacao/serie.py::metricas`.

| Métrica do DoD | Chave |
| --- | --- |
| tempo sem mudança de posição | `minutos_sem_mudanca` (trecho atual) e `maior_minutos_sem_mudanca` |
| reposicionamentos por hora | `reposicionamentos_por_hora` (com `mudancas_de_posicao` e `horas_observadas`) |
| nº de saídas do leito | `saidas_do_leito` e `saidas_do_leito_noite` |
| (extra) | `quedas` |

As métricas vão em `evidence.features` de **todo** evento (no instante do
achado) e em `<nome>_relatorio.json` (série inteira, por estadia no MIMIC).
Testes: `test_metricas*` em `tests/movimentacao/test_serie.py`.

### 3. Alerta de imobilidade > 2 h, limiar configurável

**Status:** ✔ — `regras.py::_imobilidade`, bloco `imobilidade` do YAML.

- Dispara quando um trecho no leito sem mudança passa **estritamente** de
  `limiar_min: 120`. Com exatamente 120 min não dispara
  (`test_imobilidade_exatamente_2h_nao_dispara`). A severidade é `media` e
  sobe para `alta` a partir de 240 min (`severidade_acima_de_min`). O score vai
  de 0,5 no limiar a 1,0 aos 240 min.
- **Configurável:** `test_limiar_configuravel_90` mostra que a mesma série
  dispara com 90 min e não com 120. O limiar e a versão vão em
  `evidence.thresholds`.
- **Sem dado não é imobilidade:** um trecho com lacuna de registro maior que
  `lacuna_max_min: 150` fica marcado como não confiável e não gera alerta
  (`test_lacuna_nao_vira_imobilidade`). O evento traz `alerta_em` (início +
  limiar), que é o instante em que um sistema em tempo real teria alertado.
- O formato bate com o exemplo do contrato
  (`contratos/exemplos/movimentacao_imobilidade_prolongada.json`): o teste
  `test_formato_igual_ao_exemplo_do_contrato` reproduz o caso de 160 min.

### 4. Queda ou saída do leito à noite = severidade alta

**Status:** ✔ — `regras.py::_saidas` e `_quedas`.

- Saída do leito: `severidade_noite: alta`, `severidade_dia: media`
  (`test_saida_noturna_alta`, `test_saida_diurna_media`). A janela que cruza a
  meia-noite está testada (`test_noite_cruza_meia_noite`: 23:30 e 05:59 são
  noite, 06:00 é dia).
- Queda: sempre `alta`, de dia ou de noite. Quedas de fontes diferentes a até 5 s
  uma da outra viram **um** evento, que lista as duas fontes
  (`test_queda_duas_fontes_um_evento`).
- Na injeção (item 6), 500/500 saídas noturnas saíram `alta` e nenhuma das 500
  saídas diurnas de controle saiu `alta`.

### 5. Desvio da baseline individual

**Status:** ✔ — `regras.py::_baseline`, bloco `baseline` do YAML.

A taxa de movimentação (mudanças de posição + saídas do leito por hora) das
últimas 6 h é comparada com a taxa das 24 h anteriores **do mesmo paciente**:

- `agitacao_subita`: taxa ≥ 2× a baseline;
- `reducao_mobilidade`: taxa ≤ 0,5× a baseline.

A comparação só acontece com pelo menos 12 h e 3 eventos de histórico
(`test_baseline_curta_nao_emite`), e com a janela atual observada sem lacuna
(`test_reducao_nao_dispara_sem_observacao`: falta de registro não é redução).
Avaliações seguidas com o mesmo sinal formam um episódio só.

**Decisão — média, não mediana.** O plano previa a mediana das taxas
horárias. Com registro a cada ~2 h, metade das horas tem zero eventos e a
mediana seria zero. Por isso a baseline usa a taxa média da janela.

### 6. Avaliação em sequências rotuladas: Recall ≥ 0,80 para quedas

**Status:** ✔ **recall 0,875** (14/16 clipes) na partição de teste, com limiar
escolhido só na calibração · ◐ veja as ressalvas abaixo.

#### Protocolo — quedas em vídeo real

- **Dados:** os 40 clipes de leito do FallVision em `conteudos/videos/`
  (28 `B_D_*` e 12 `B_N_*`, de 1 a 6 s, 24 a 120 fps).
- **Rótulos:** [`data/eval/us17/quedas.csv`](../data/eval/us17/quedas.csv), feitos
  por inspeção visual de 8 quadros por clipe, sem olhar a saída do detector.
  Os 28 `B_D_*` mostram queda do leito ao chão e os 12 `B_N_*` mostram a pessoa
  deitada. Cinco pares são a mesma cena reencodada e ficam no mesmo `grupo`.
- **Partição:** paridade do MD5 do `grupo`, a mesma regra da US08. Calibração:
  12 clipes de queda (9 cenas) e 7 normais. Teste: 16 clipes de queda (14 cenas) e
  5 normais. Os limiares foram ajustados **só** na calibração.
- **Unidade:** o clipe. Queda detectada = pelo menos um evento `queda`.
  Alarme falso = evento `queda` num clipe normal.

#### O que mudou em relação ao plano

O plano previa detectar queda pela **forma** da caixa da pessoa (em pé → larga
e baixa), como segundo sinal além da pose da US08, que teve recall 0,23. Nos
clipes reais, quem cai do leito **já estava deitado**, então a caixa já era
larga antes da queda. Nesses clipes, a regra de forma pega 3 de 28 quedas. O
que muda é a **posição**: o centro da caixa desce na imagem e fica embaixo.
Daí a regra `quedas_por_descida`:

- o centro vertical da caixa passa por uma mediana móvel de 0,3 s, para que um
  quadro espúrio não vire queda;
- há queda quando o centro desce ≥ `descida_min_frac` da altura do quadro em
  até 1,5 s e fica embaixo por 0,5 s (ou até o fim do clipe);
- **calibração:** 0,065 é o maior limiar que mantém recall 1,0 na partição de
  calibração. Recall tem prioridade, como na US08.

Também mudou o detector. Com o padrão da US07 (confiança 0,25, 640 px,
ByteTrack), a pessoa aparece em 27% dos quadros dos clipes de queda, porque o
tracker descarta caixas de baixa confiança. Sem tracker, com confiança 0,1 e
960 px (bloco `video.detector`), aparece em 58%. O padrão da US07 não foi
alterado.

#### Resultados

| Regras | Partição | Recall | IC 95% (Wilson) | Detectadas | Alarmes falsos |
| --- | --- | --- | --- | --- | --- |
| **descida (padrão)** | calibração | 1,000 | 0,76–1,00 | 12/12 | 2/7 |
| **descida (padrão)** | **teste** | **0,875** | 0,64–0,97 | **14/16** | 1/5 |
| descida | total | 0,929 | 0,77–0,98 | 26/28 | 3/12 |
| forma da caixa | teste | 0,125 | 0,04–0,36 | 2/16 | 1/5 |
| descida + forma | calibração | 1,000 | 0,76–1,00 | 12/12 | 3/7 |
| descida + forma | teste | 0,938 | 0,72–0,99 | 15/16 | 1/5 |

O padrão é **só descida**. Na calibração, acrescentar a forma não aumentou o
recall e acrescentou um alarme falso (`REGRAS_QUEDA_PADRAO` em
`movimentacao/video.py`). A combinação foi escolhida pela calibração, não pelo
teste.

- **Perdidas no teste:** `B_D_0007_resized` e `B_D_0009_resized`. São os dois
  clipes com câmera vista de cima, em que cair do colchão ao chão não aparece
  como descida na imagem.
- **Alarme falso no teste:** `B_N_105`, em que a pessoa aparece em só 16% dos
  quadros e as poucas caixas oscilam. Na calibração, `B_N_87_resized` e
  `B_N_96` têm a pessoa em quase todos os quadros. O alarme vem da oscilação
  da caixa de quem está deitado, que passa de 0,065 da altura do quadro.

**Ressalvas — leia antes de citar o número:**

1. **Amostra pequena.** São 16 clipes de queda no teste, e o IC 95% vai de 0,64 a
   0,97. Contando cenas únicas (14), o recall é 12/14 = 0,857 (IC 0,60–0,96).
   A meta é cumprida na estimativa pontual. O limite inferior do intervalo fica
   abaixo de 0,80.
2. **Taxa de alarme falso alta:** 1/5 no teste e 3/12 no total. São poucos
   clipes normais, todos com a pessoa parada no leito.
3. **Rótulos sem conferência humana.** Os rótulos foram feitos por inspeção
   visual feita pelo assistente, e o README dos rótulos pede essa conferência.
4. **A pose da US08 não entrou na medida.** Sem os pesos do OpenPose, a fonte
   `video_pose` não foi avaliada nos clipes. Na operação, ela soma à descida
   pela regra de fusão de quedas.

#### Casos injetados em séries reais do MIMIC (os cinco tipos)

O demo não tem episódios rotulados de queda, saída noturna ou agitação. Por
isso [`movimentacao/injecao.py`](../movimentacao/injecao.py) pega a série real
de uma estadia até um instante sorteado, logo depois de um registro real, e
injeta um padrão conhecido. São 5 seeds × 100 casos por cenário.

| Tipo | Cenário | Recall | Controles com alarme |
| --- | --- | --- | --- |
| `queda` | dia / noite | 1,000 (1000/1000) | — |
| `saida_do_leito` | noite → alta | 1,000 (500/500) | dia → alta: 0/500 |
| `imobilidade_prolongada` | 130–240 min | 1,000 (500/500) | 60–100 min: 0/500 |
| `agitacao_subita` | max(3, 3× baseline) trocas/h por 6 h | 0,988 (494/500) | — |
| `reducao_mobilidade` | 6 h sem troca | 0,822 (411/500) | — |

Esses números verificam a lógica sobre o histórico real de cada paciente. Não
medem a percepção, porque os padrões injetados são nítidos de propósito. As
perdas são esperadas pela regra:

- **agitação:** pacientes com baseline no limite mínimo (0,12–0,23
  trocas/h). Quando a janela de avaliação desliza, ficam menos de 3 eventos de
  histórico e a comparação não acontece;
- **redução:** a troca real registrada logo antes da injeção cai na janela
  atual de 6 h e deixa a taxa acima de 0,5× a baseline.

#### Volume de achados nos dados reais (sem rótulo)

São 139 estadias e 488 paciente-dias do MIMIC-IV Demo:

| Tipo | Achados | Por paciente-dia |
| --- | --- | --- |
| `imobilidade_prolongada` | 453 (38 com ≥ 4 h, alta) | 0,93 |
| `saida_do_leito` | 179 (6 noturnas, alta) | 0,37 |
| `agitacao_subita` | 129 | 0,26 |
| `reducao_mobilidade` | 79 | 0,16 |

Um achado real típico é `imobilidade_prolongada`: "2 h 03 min sem mudança de
posição no leito", `minutos_sem_mudanca: 123`, `limiar_min: 120`. A enfermagem
registra o reposicionamento por volta de 2 h, então atrasos de minutos já
disparam. Pela referência NPIAP/EPUAP isso está correto, mas é ~1 alerta por
paciente-dia. Se a equipe quiser menos alertas, o caminho é ajustar
`limiar_min` (configurável), não o código.

## Contrato (US04)

- A modalidade `movimentacao` e os cinco `event_type` já estavam no catálogo:
  **nada mudou no schema**.
- `model_version = anomalias-movimentacao@0.1.0`. `evidence.thresholds` traz
  `versao_limiares` em todo evento.
- `patient_id` e `encounter_id` são pseudonimizados por HMAC com
  `PSEUDONYM_KEY`. Nenhum `subject_id` cru aparece no evento
  (`test_ids_pseudonimizados`).
- Sem achados, a série gera um `sem_achados` (`info`) com as métricas.
- `python -m contratos.validar saida/movimentacao/mimic_eventos.jsonl` →
  861 válidos, 0 inválidos.

## Testes e reprodução

```bash
uv run pytest tests/movimentacao        # 119 testes da US17
uv run pytest                           # suíte inteira: 572 passam, 2 pulados (smoke Azure)
uv run python scripts/avaliar_us17.py   # ~6 s com o cache de caixas; ~6 min sem ele (roda o YOLO)
```

O cache `saida/movimentacao/caixas_us17.json` guarda só as coordenadas da maior
pessoa por quadro e é regenerado se for apagado. Com ele e sem ele, o resultado
foi o mesmo.

## Limitações e próximos passos

- **Postura pela pose:** rodar a US06 em vídeo de leito real e calibrar
  `razao_lateral_max` e `inclinacao_sentado_max` para a câmera.
- **Câmera vista de cima:** as duas quedas perdidas são desse tipo. A descida
  na imagem não serve para essa câmera. Seria preciso medir saída da ROI do leito
  ou deslocamento em qualquer direção, calibrado por câmera.
- **Quem está na ROI:** a US07 não distingue paciente de profissional. Uma
  pessoa da equipe na lateral do leito vira "saída do leito".
- **Registro não é sensor:** no MIMIC, ausência de registro não prova
  imobilidade (por isso existe `lacuna_max_min`), e `Activity = Chair` marca o
  instante do registro, não o da saída.
- **Período noturno no MIMIC:** o fuso padrão é UTC, aplicado aos horários
  como estão no demo.
- **Avaliação de queda:** precisa de mais clipes normais e de quedas de pé
  (indo ao banheiro), para medir a regra de forma da caixa, que hoje não tem
  dado próprio.

Alterações em arquivos que já existiam:
[`docs/us17_alteracoes_em_codigo_existente.md`](us17_alteracoes_em_codigo_existente.md).
