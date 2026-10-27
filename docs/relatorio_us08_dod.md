# Relatório US08 — rastreabilidade ao Definition of Done

**US08 · Detectar movimentos e eventos fora do padrão no vídeo.** A fonte de
verdade é o enunciado do Tech Challenge (Fase 4, "1. Análise de Vídeo"):
*processar vídeos clínicos (fisioterapia ou cirurgia), detectar movimentos ou
eventos fora do padrão esperado com OpenPose (análise postural) e YOLOv8
(objetos e áreas críticas) e gerar relatórios automáticos indicando desvios ou
falhas no procedimento.* O DoD abaixo é a proposta do grupo para cumprir esse
item; cada linha aponta o código, a configuração e o *status* real.

Os caminhos são relativos à raiz do repositório.

## Fluxo

```text
vídeo ─► US06 OpenPose ─► keypoints + ângulos por quadro ─┐
     └─► US07 YOLOv8 ─► entrada/saída de área crítica ────┤
                                                          ▼
              config/us08_padroes.yaml (perfil do tipo de vídeo, limiares versionados)
                                                          ▼
     video/desvios/regras.py + areas.py ─► desvios que persistiram pela janela mínima
                                                          ▼
     video/desvios/eventos.py ─► EventoAchado (US04) ─► fusão (US18) / alertas (US19)
     video/desvios/evidencia.py ─► quadro de evidência anonimizado (JPG)
     video/desvios/run.py ─► <fonte>_eventos.jsonl + <fonte>_relatorio.json
```

A US08 não chama a US06 nem a US07: lê o que elas gravam (JSON de pose e JSONL de
eventos). Assim cada US roda e é testada sozinha, como pede a arquitetura
([`docs/arquitetura/README.md`](arquitetura/README.md)).

## Quadro DoD → código

### 1. "Padrão esperado" definido explicitamente por tipo de vídeo

**Status:** ✔ implementado.

[`config/us08_padroes.yaml`](../config/us08_padroes.yaml) tem um perfil por tipo de
vídeo (`tipo_video`: `fisioterapia`, `leito`, `cirurgia`), cada um com
`descricao` do padrão em português e as regras que o verificam:

| Perfil | Padrão esperado |
| --- | --- |
| `fisioterapia_geral` | tronco até 30°, joelho não abaixo de 45°, assimetria até 20° (os critérios do feedback da US06) |
| `fisioterapia_abducao_ombro_frontal` | as 3 primeiras repetições (supervisionadas) definem amplitude do ombro e extensão do cotovelo; as seguintes ficam a ±10° delas; tronco até 20°; série de 5 a 10 repetições |
| `fisioterapia_agachamento_frontal` | joelhos flexionam juntos (assimetria até 25°); 5 a 10 repetições |
| `fisioterapia_agachamento_meio_perfil` | tronco até 20° de inclinação |
| `internacao_leito` | sem queda, sem giro brusco do tronco, ninguém na borda do leito por mais de 3 s |
| `cirurgia` | ninguém na área restrita, nenhum celular/garrafa/copo no campo estéril |

- **Leitura e validação:** `video/desvios/config.py` recusa chave desconhecida,
  coluna de ângulo inexistente, severidade fora do catálogo e combinação
  incoerente (alvo aquém do repouso), na carga.
- **Por exercício e por vista:** os ângulos da US06 são projeções 2D, então o
  mesmo exercício tem padrões diferentes de frente e a 45° (no agachamento a 45°
  a "assimetria" dos joelhos é efeito da projeção; o desvio visível é o tronco).
- **Repetições:** `video/desvios/regras.py::repeticoes` separa as repetições
  pelo ângulo principal (trecho fora do `repouso`), conta e compara com
  `esperadas`. Repetição cortada no início ou no fim do vídeo não conta.

**Decisão — padrão do próprio paciente na abdução.** A primeira versão usava um
alvo absoluto (ombro ≥ 130°), calibrado em PM_001, em que as corretas chegam a
132–138° e as incorretas a 122–129°. Em PM_012 é o contrário: corretas 119–126°,
incorretas 140–149°. Ângulo 2D "certo" depende da pessoa e da câmera, então o
perfil passou a usar `referencia`: as 3 primeiras repetições da sessão, feitas
com o fisioterapeuta, viram o padrão daquele paciente, e cada repetição
seguinte é comparada a ele na fase de pico (`fase_pico_graus`). O alvo absoluto
continua disponível (`alvo`) para exercícios em que ele generaliza.

### 2. Detecta: postura fora da faixa, assimetria, mudança brusca/queda, pessoa/objeto em área crítica

**Status:** ✔ implementado — os quatro `event_type` já estavam no catálogo da
modalidade `video` (`contratos/catalogo.py`), nenhum schema mudou.

| Desvio | Regra | `event_type` |
| --- | --- | --- |
| Ângulo fora de `[min, max]` | `regras.py::desvios_de_faixa` | `postura_fora_da_faixa` |
| Repetição sem amplitude ou fora da referência | `regras.py::repeticoes` | `postura_fora_da_faixa` |
| \|esq − dir\| acima do limiar | `regras.py::desvios_de_faixa` (colunas `assimetria_*`) | `assimetria_movimento` |
| Queda: centro do quadril desce ≥ 0,7 tronco em ≤ 1 s e não volta | `regras.py::desvios_de_queda` + `sinais.py::saltos_mantidos` | `mudanca_brusca_ou_queda` |
| Mudança brusca: tronco gira ≥ 60° em ≤ 0,5 s e fica | `regras.py::desvios_de_mudanca_brusca` | `mudanca_brusca_ou_queda` |
| Classe proibida em ROI da US07 por mais que o tolerado | `areas.py::desvios_de_areas` | `presenca_indevida_area_critica` |

- A queda usa o **comprimento do tronco** (pescoço→quadril) como escala, o que
  independe da distância da câmera e da resolução.
- Depois da queda a pessoa costuma sair do quadro ou ficar ocluída; quadro sem
  keypoint não conta como "levantou" (`ausencia_mantem`), só uma medida de volta
  acima conta. Sem isso duas quedas de calibração (B_N_43 e B_N_45) escapavam.
- Giro de tronco que acontece junto com uma queda não gera segundo evento.
- Presença indevida pareia `entrada`/`saida` do mesmo `track_id` na mesma área e
  cita os dois eventos da US07 em `related_event_ids`; uma entrada sem saída
  vale até o fim do vídeo.
- Sem pose, um perfil misto (leito) avalia só as áreas e se declara não
  confiável, para não publicar `sem_achados` sobre postura não avaliada.

### 3. Evento só dispara se o desvio persistir por janela mínima configurável

**Status:** ✔ implementado.

- `geral.janela_minima_s: 1.0` é o padrão; cada regra pode ter `janela_s`
  própria (0,5 s na queda e no tronco do agachamento a 45°, 0,8 s na assimetria
  do agachamento, 3 s na borda do leito).
- `sinais.py::trechos` junta trechos separados por até `geral.lacuna_max_s`
  (0,3 s, um ou dois quadros perdidos) e descarta os mais curtos que a janela.
- Queda e mudança brusca exigem que a posição nova se mantenha por `janela_s`
  com pelo menos `fracao_permanencia` (70%) do salto; vídeo que acaba antes não
  confirma.
- A janela e a versão dos limiares vão em `evidence.thresholds` de cada evento.

### 4. Avaliação em vídeos rotulados: Recall ≥ 0,80 e Precision ≥ 0,70 por evento

**Status:** ✘ meta **não atingida** no teste (precisão 0,58, recall 0,65). Medida, protocolo e causas abaixo.

**Dados rotulados (normal × desvio):**

- **REHAB24-6** (`data/rehab24/Segmentation.csv`): cada repetição tem
  `correctness` (0 = execução incorreta). Avaliados o exercício 1 (abdução, vista
  frontal) e o exercício 6 (agachamento, frontal e a 45°), câmera 17.
- **FallVision** (`data/fallvision`): clipes de 2–8 s rotulados pela pasta
  `Fall` / `No Fall` (cenário leito). Amostra sorteada (semente 8) de 20 + 20.

**Protocolo** (`scripts/avaliar_us08.py`, `video/desvios/avaliacao.py`):

- Unidade = repetição (REHAB24-6) ou clipe (FallVision). Cada evento é
  atribuído à unidade com que mais se sobrepõe; evento em unidade com desvio é
  acerto, em unidade normal ou entre repetições é falso positivo.
  `precisão = eventos certos / eventos`, `recall = unidades com desvio detectadas / unidades com desvio`.
- Cada vista do REHAB24-6 é uma sessão (o vídeo é recortado no trecho dela).
  Com perfil de referência, as 3 primeiras repetições da sessão não entram na
  conta (são a série supervisionada).
- **Calibração × teste.** Limiares ajustados só em PM_001, PM_012 (abdução),
  PM_029 (agachamento) e nos 18 clipes do FallVision com MD5 do id par. A meta é
  medida no resto: pessoas e clipes que não foram vistos ao calibrar. PM_012
  começou no teste e foi movido para a calibração porque foi ele que revelou o
  problema do alvo absoluto.
- Extração de pose a ~1,4 s/quadro em CPU limitou o conjunto de teste; a ordem
  dos vídeos foi fixada antes de qualquer resultado.

**Resultados** (limiares v1.1.0):

| Partição | Grupo | Precisão | Recall | Eventos | Desvios detectados |
| --- | --- | ---: | ---: | ---: | ---: |
| calibração | abdução frontal (PM_001, PM_012) | 1,00 | 1,00 | 10 | 10/10 |
| calibração | agachamento frontal (PM_029) | 1,00 | 1,00 | 5 | 5/5 |
| calibração | agachamento 45° (PM_029) | 1,00 | 0,80 | 5 | 4/5 |
| calibração | queda FallVision (18 clipes) | 0,83 | 0,63 | 6 | 5/8 |
| calibração | **total** | **0,96** | **0,86** | 26 | 24/28 |
| teste | abdução frontal (PM_023) | **1,00** | **0,80** | 4 | 4/5 |
| teste | agachamento frontal (PM_038, PM_043) | 0,63 | 1,00 | 8 | 5/5 |
| teste | agachamento 45° (PM_038, PM_043) | 0,50 | 1,00 | 28 | 8/8 |
| teste | queda FallVision (24 clipes) | 0,60 | 0,23 | 5 | 3/13 |
| teste | **total** | **0,58** | **0,65** | 45 | 20/31 |

Detalhe por vídeo e por unidade (perdidas e alarmes falsos):
`saida/video/avaliacao_us08.json`, gerado por `scripts/avaliar_us08.py`.

**Leitura dos resultados:**

- **Abdução com série de referência: generaliza.** Em uma pessoa não vista
  (PM_023), precisão 1,00 e recall 0,80, dentro da meta. É o resultado que valida
  a decisão do item 1: comparar com o padrão do próprio paciente.
- **Agachamento com limiar absoluto: não generaliza.** Recall 1,00 nas duas
  vistas, mas com alarmes falsos em repetições corretas: a 45° as corretas de
  PM_038 e PM_043 já inclinam o tronco mais que 20° (todas as 12 repetições
  corretas receberam evento), e de frente 3 das 15 corretas (PM_038 #8–#10)
  também receberam. É o mesmo problema que a abdução teve com o alvo absoluto, agora
  visto no teste. O próximo passo é dar ao agachamento uma `referencia` (joelhos,
  quadril e tronco na fase de pico) e validar nos vídeos de Ex6 ainda não vistos
  (PM_105, PM_126). Ajustar o limiar olhando PM_038/PM_043 contaminaria o teste.
- **Queda só por pose: fraca no FallVision.** Recall 0,23 (3 de 13). Em 4 das 10
  quedas perdidas a pessoa aparece em menos de 50% dos quadros (o relatório marca
  esses vídeos como pouco confiáveis); nos demais, os clipes de 2–3 s terminam
  logo depois do impacto ou a descida do quadril é lenta. Houve 2 alarmes falsos
  em 11 clipes normais (deitar/sentar rápido). Os clipes curtos e redimensionados
  do FallVision são mais difíceis que uma câmera de leito contínua, mas o número
  mostra que queda precisa de um segundo sinal (a caixa `person` da US07).
- **Sem o agachamento a 45°** (o grupo com mais alarmes falsos), o total de
  teste seria precisão 0,71 (12/17) e recall 0,52 (12/23): a queda segura o recall.

### 5. Ao menos 1 desvio real detectado documentado com timestamp e quadro de evidência

**Status:** ✔ — dois desvios reais, em vídeos de dataset (não sintéticos).

**Queda — FallVision `Fall/Bed/Raw Video/f_raw_b_3/B_N_03.mp4`, perfil `internacao_leito`.**
O quadril desceu 1,1 comprimento de tronco em 0,5 s e ficou embaixo: evento
`mudanca_brusca_ou_queda`, severidade `alta`, janela 0,4–2,0 s do vídeo, quadro
216 (1,80 s).

![Queda em B_N_03](evidencias/us08/fallvision_B_N_03_queda_1.80s.jpg)

```json
{"event_type": "mudanca_brusca_ou_queda", "severity": "alta", "score": 0.7087,
 "timestamp": "2026-10-05T19:00:00.400Z",
 "window": {"start": "2026-10-05T19:00:00.400Z", "end": "2026-10-05T19:00:02.000Z"},
 "evidence": {"summary": "Possível queda aos 0,9 s: o quadril desceu 1,1 comprimentos de tronco em 0,5 s e ficou embaixo por 1,1 s.",
   "features": {"regra": "queda", "valor": 1.117, "pico_video_s": 1.8, "frame_index": 216, "tronco_px": 330.9},
   "thresholds": {"versao_limiares": "1.1.0", "perfil": "internacao_leito", "limiar": 0.7, "janela_minima_s": 0.5, "lacuna_max_s": 0.3},
   "artifacts": [{"kind": "frame", "uri": "saida/video/desvios/B_N_03/00_queda_0001.80s.jpg"}]},
 "model_version": "video-desvios@0.1.0"}
```

**Repetição incorreta — REHAB24-6 `Ex1/PM_001-Camera17-30fps.mp4`, perfil `fisioterapia_abducao_ombro_frontal`.**
A repetição 8 (rotulada incorreta, quadros 2347–2554 = 78,2–85,1 s do arquivo)
parou em 122° de abdução com o cotovelo travado em 174°, contra 135° e 160° nas
3 repetições de referência. Quadro de evidência 2454 (81,8 s).

![Repetição incorreta em PM_001](evidencias/us08/rehab24_PM_001_repeticao8_81.80s.jpg)

```json
{"event_type": "postura_fora_da_faixa", "severity": "baixa", "score": 0.5653,
 "timestamp": "2026-10-05T19:01:19.500Z",
 "window": {"start": "2026-10-05T19:01:19.500Z", "end": "2026-10-05T19:01:23.400Z"},
 "evidence": {"summary": "Na repetição 8 (79,5 a 83,4 s) o movimento saiu do padrão das 3 repetições de referência: ângulo do ombro direito em 122° (referência 135°, tolerância ±10°); ângulo do cotovelo direito em 174° (referência 160°, tolerância ±10°).",
   "features": {"regra": "repeticao.referencia", "medida": "cotovelo_direito", "valor": 173.78, "frame_index": 2454,
     "referencia_ombro_direito": 135.31, "referencia_cotovelo_direito": 159.86,
     "diferenca_ombro_direito": -13.31, "diferenca_cotovelo_direito": 13.92},
   "thresholds": {"versao_limiares": "1.1.0", "perfil": "fisioterapia_abducao_ombro_frontal", "limiar": 10.0, "janela_minima_s": 1.0}},
 "model_version": "video-desvios@0.1.0"}
```

Para reproduzir:

```bash
uv run python -m video.desvios.run --perfil internacao_leito \
  --pose outputs/pose/Fall__Bed__Raw_Video__f_raw_b_3__B_N_03.json \
  --patient-id pt_8e5b4d1cfc60a16434dc03f0 --inicio-utc 2026-10-05T19:00:00Z --source-id B_N_03
```

O quadro sai **anonimizado**: com pose, a cabeça é tarjada a partir dos keypoints
do rosto (`evidencia.py::_tarjar_cabeca`); sem pose, vale o YuNet da US05. O
YuNet com limiar baixo gerava tarjas falsas grandes que escondiam o próprio
braço da evidência, por isso não é usado quando há keypoints.

### 6. Limiares versionados em arquivo de configuração (não hard-coded)

**Status:** ✔ implementado.

- Todos os limiares, janelas, severidades e o score estão em
  `config/us08_padroes.yaml`, com `versao` (hoje `1.1.0`) e o motivo de cada
  número calibrado em comentário. O código só tem os nomes das regras.
- Cada evento carrega `versao_limiares`, `perfil`, `limiar`, `janela_minima_s` e
  `lacuna_max_s` em `evidence.thresholds`: dá para saber com que limiares um
  achado antigo foi gerado.
- Histórico: 1.0.0 (alvo absoluto na abdução, queda com descida 0,8) → 1.1.0
  (referência do paciente na abdução, queda 0,7 com oclusão tolerada).

## Relatório automático

`python -m video.desvios.run` grava `<fonte>_relatorio.json` com o padrão
aplicado (`padrao_esperado`), versão dos limiares, cobertura de pose, avisos
(repetições fora do esperado, pouca pessoa visível), a lista de repetições com
pico e medidas, e cada desvio com `timestamp`, trecho do vídeo, resumo em
português e caminho do quadro de evidência. É a base da US09.

## Limitações

- **Ângulos 2D.** Dependem da posição da câmera; por isso os perfis são por
  vista e a abdução usa o padrão do próprio paciente.
- **Queda depende do OpenPose ver a pessoa durante a descida.** Nos clipes em
  que a pose se perde (cobertura de 45–75%), a descida não aparece e a queda
  escapa. Uma saída é combinar com a caixa `person` da US07 (proporção e
  descida da caixa), que não depende de keypoints.
- **Sentar ou deitar rápido** (ex. B_N_36) passa pela regra de queda: descida
  de ~0,9 tronco em 1 s e posição mantida.
- **Instrumento cirúrgico dentro do campo** exige o modelo com classes médicas
  (fine-tuning da US07). Com pesos COCO o perfil `cirurgia` vigia pessoas na
  área restrita e objetos comuns no campo estéril.
- **Área crítica sem dataset rotulado.** A regra foi validada com eventos
  sintéticos e com o JSONL real da US07 (`saida/video/eventos_us07.jsonl`:
  pessoa 0,3 s na lateral do leito, abaixo do tolerado, nenhum desvio), mas não
  tem precisão/recall medidos.
- **Pessoa já dentro da área no início do vídeo** não gera `entrada` na US07
  (`primeira_observacao_entra: false`) e, portanto, não gera presença indevida.

## Testes

`uv run pytest tests/video/test_desvios.py` — configuração (inclusive
recusas), trechos persistentes, saltos mantidos e oclusão, cada regra, série
de referência, pareamento de área, contrato da US04, avaliação e a CLI ponta a
ponta com pose e vídeo sintéticos (sem OpenPose).
