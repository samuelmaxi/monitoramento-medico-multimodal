# Relatório US07 — rastreabilidade ao Definition of Done

Rastreamento de cada item do *Definition of Done* (DoD) da US07 para o código
implementado, com evidência (`arquivo:linha`), valores de configuração, *status* e
a justificativa de projeto ("porquê") por decisão. Os caminhos são relativos à raiz
do repositório.

## Considerações de projeto (porquê)

- **IoU ≥ 0,5 como critério de acerto** é o padrão PASCAL VOC (avaliação
  `mAP@0.5`), que é a mesma família usada pelo Ultralytics no validator. O mesmo
  valor é usado no NMS de inferência (`iou`).
- **`conf = 0,25`** é o limiar padrão do Ultralytics; documentos e default do
  projeto seguem o padrão para reprodutibilidade.
- **Rastreamento por `track_id` (ByteTrack)** separa indivíduos e preserva estado
  por objeto; eventos de entrada/saída são emitidos por transição de estado
  `(área, track)`.
- **ROIs em resolução de referência + escala** permitem desenhar polígonos uma vez
  por câmera e reutilizá-los em vídeos de resolução diferente.
- **JSONL por vídeo sempre criado**: a ausência de detecções é um resultado
  legítimo de processamento e precisa ficar registrado; sem isso não dá para
  distinguir "vídeo processado sem eventos" de "vídeo não processado". Quando não
  há transição de entrada/saída, o pipeline emite um registro `sem_achados`
  (severidade `info`, `score 0.0`) com o resumo do processo (quadros lidos,
  detecções, duração, classes), relatando explicitamente que não houve queda ou
  saída (`video/eventos.py::montar_sem_achados`).
- **Métricas medidas, não copiadas nem estimadas**: sem fine-tuning, o braço
  do DoD é "modelo pré-treinado COCO + validação qualitativa". As quatro métricas
  do `yolov8n.pt` foram **medidas** no COCO val2017 (5000 imagens) com o mesmo
  `scripts/avaliar_us07.py` do item 5; o mAP@0.5:0.95 medido (0,366) confere com
  o model card (0,373). Métricas no domínio clínico continuam dependendo de
  dataset anotado (item 5).
- **Entrada e saída pareadas**: só há `saida_area_critica` de um track que teve
  `entrada_area_critica`. Sem isso, fragmentos do ByteTrack (track novo nascido
  já dentro da área) geravam "saídas" sem entrada — 4 dos 6 eventos da rodada
  anterior.

## Quadro DoD → código

### 1. YOLOv8 aplicado aos vídeos + classes de interesse listadas e justificadas

**Status:** ✔ implementado.

- **Código:** orquestração em `scripts/rodar_us07_video.py`; detector e parsing em
  `video/detector.py` (`DetectorYolo.detectar`, `_inferir`); filtro de classes em
  `video/detector.py::ids_das_classes` (`video/detector.py:139`).
- **Config (classes e justificativa):** `config/exemplo_us07.json` →
  `detector.classes: ["person", "bed", "chair", "couch", "bottle"]`.
  - `person` — ator central do monitoramento (paciente/cuidador);
  - `bed`, `chair`, `couch` — móveis do quarto que definem o contexto de "borda do
    leito", "lateral do leito" e áreas de risco;
  - `bottle` — objeto de fisioterapia/ambiente comumente presente.
  - O checkpoint COCO não cobre classes clínicas (instrumentos cirúrgicos etc.);
    essas exigem dataset próprio e fine-tuning (item 5), não são inferidas.
- **Justificativa:** cobre o cenário assistencial que o exemplo exercita sem
  exigir fine-tuning prévio.

### 2. Áreas críticas como ROIs (polígonos) configuráveis por vídeo/câmera

**Status:** ✔ implementado — `config/leito_uti_07.json` tem duas ROIs: `lateral_leito`
(retângulo) e `borda_leito` (trapézio na borda do colchão por onde o paciente sai da
cama — risco de queda). Prévia desenhada: `saida/video/rois_leito_uti_07.jpg`.

- **Código:** modelo/validação em `video/areas.py`
  (`AreaCrud`, `para_escala` em `video/areas.py:238`, ponto/função de contenção);
  geometria em `video/geometria.py` (clipping e `fracao_da_caixa_dentro`).
- **Config:** `config/exemplo_us07.json` → `areas[]` com `points`, `largura_ref`/
  `altura_ref`, `area_minima`, `classe_filtro`. Cada câmera/vídeo tem seu arquivo
  de config (`config/exemplo_us07.json`, `config/leito_uti_07.json`) e a CLI
  aceita `--video`/`--videos-dir` para rotear vídeos.
- **Justificativa:** resolução de referência + escala (`video/areas.py:145`)
  permite calibrar o polígono uma vez por câmera e aplicar a vídeos de resoluções
  diferentes.

### 3. Critério de acerto IoU ≥ 0,5 (PASCAL VOC / mAP@0.5)

**Status:** ✔ implementado.

- **Código (NMS):** `video/detector.py::_inferir` envia `iou` ao Ultralytics
  (`argumentos["iou"]`); valor default em `video/detector.py` (`ConfiguracaoDetector`).
- **Código (critério/métrica nativos):** `video/avaliacao_deteccao.py` casa
  predições e verdades por `IoU >= 0,50` da mesma classe (`casar_deteccoes`) e
  calcula TP/FP/FN, precisão/revocação/F1 e **AP@0.5 / mAP@0.5** por interpolação
  em todos os pontos (VOC2010+). CLI: `scripts/avaliar_voc_us07.py`; o resultado
  alimenta o gate (`avaliar_gate`) e fica não medido (`None`) sem ground truth.
- **Config:** `config/exemplo_us07.json` → `detector.iou: 0.5`.
- **Justificativa:** mesmo limiar de IoU da métrica `mAP@0.5` (PASCAL VOC)
  garante coerência entre detecção e avaliação; o avaliador nativo permite medir
  o critério sem depender do validator da Ultralytics.
- **Teste:** `tests/video/test_detector.py` (assert de `argumentos["iou"]`);
  `tests/video/test_avaliacao_deteccao.py` e `tests/video/test_cli_avaliar_voc.py`
  (limite exato de IoU 0,50, classe divergente, TP/FP/FN, AP, gate).

### 4. Limiar de confiança documentado (padrão Ultralytics conf = 0,25)

**Status:** ✔ implementado e documentado.

- **Código/config:** `config/exemplo_us07.json` → `detector.confianca: 0.25`;
  default em `video/detector.py`.
- **Docs:** `README.md` e `docs/arquitetura/us07_deteccao_areas_criticas.md`
  ("Decisões e parâmetros").
- **Justificativa:** `0.25` é o default Ultralytics; manter o default facilita
  comparar com a literatura e com o validator.

### 5. Fine-tuning em classes médicas com mAP@0.5 ≥ 0,50 no val

**Status:** ✔ gate implementado; resultado real depende de dataset anotado.

- **Rota:** `scripts/dataset_us07.py treinar` exige dataset apto; após o treino
  avalia `best.pt` (ou `last.pt`) no split `val`, verifica o mAP@0.5 **de cada
  classe médica** e aplica o gate ≥ 0,50. Uma média global alta não mascara uma
  classe médica abaixo da meta. O subcomando `validar-finetuning --pesos ...`
  permite reavaliar checkpoints já treinados.
- **Relatório:** `dataset_us07/reports/fine_tuning_val.json`, com pesos, taxonomia,
  métricas globais e AP50 por classe; exit code 0 aprovado, 1 reprovado/não medido.
  Se o validator não fornecer AP para alguma classe médica, o critério fica
  explicitamente não medido e não aprovado.
- **Limitação de evidência:** o repositório não contém labels médicas reais; não
  se declara atingida a meta até anotar os três splits e executar a validação.

### 6. Métricas mAP@0.5, mAP@0.5:0.95, Precision e Recall registradas no relatório

**Status:** ✔ registradas no relatório — as quatro medidas no COCO val2017 (braço sem fine-tuning).

- **Quantitativa real (dataset anotado):** `scripts/avaliar_us07.py` roda o
  validator Ultralytics e grava `mAP@0.5`, `mAP@0.5:0.95`, precision, recall e
  metadados; o gate salva `dataset_us07/reports/avaliacao_<split>.json`.
- **Braço sem fine-tuning (vigente):** a seção `avaliacao` do
  `relatorio_us07.json` registra `metricas_coco_baseline()` (`video/metricas.py`),
  com os valores medidos (resultado completo, inclusive AP por classe, em
  `saida/video/metricas_coco_val2017.json`):

| Métrica (COCO val2017, `yolov8n.pt`, conf 0,001, IoU NMS 0,5) | Valor |
  |---|---|
  | mAP@0.5 | **0,528** |
  | mAP@0.5:0.95 | **0,366** (model card publica 0,373) |
  | Precision | **0,637** |
  | Recall | **0,487** |
  | AP@0.5 das classes de interesse | person 0,503 · bed 0,425 · couch 0,431 · bottle 0,296 · chair 0,253 |

  Como reproduzir:

  ```bash
  # COCO val2017 (5000 imagens) fora do repositório, ex.: ~/datasets/coco_val2017
  #   imagens: http://images.cocodataset.org/zips/val2017.zip
  #   labels:  https://github.com/ultralytics/assets/releases/download/v0.0.0/coco2017labels.zip (só labels/val2017)
  #   data.yaml: path, train/val = images/val2017 e os 80 names de ultralytics/cfg/datasets/coco.yaml
  uv run python scripts/avaliar_us07.py ~/datasets/coco_val2017/data.yaml \
    --weights yolov8n.pt --split val --output saida/video/metricas_coco_val2017.json
  ```

- **Validação qualitativa:** `qualitativa.videos_anotados` aponta
  `saida/video/video_anotado_leito_uti_07.mp4` (caixas, track e ROIs por quadro).
  `quantitativa_em_ground_truth` segue `null` até existir dataset clínico anotado.
- **Teste:** `tests/video/test_metricas.py` e `tests/video/test_cli_avaliar.py`.

### 7. Evento emitido (schema US04) quando pessoa/objeto entra ou sai de área crítica, com timestamp

**Status:** ✔ implementado.

- **Código:** tradução para o contrato em `video/eventos.py`
  (`TradutorEventos.montar`/`montar_sem_achados`); orquestração em
  `video/pipeline.py`; emissão `contratos/emissor.py` (`EmissorJsonl`);
  validação de schema em `contratos/validar.py`.
- **Saída:** `saida/video/eventos_<stem>.jsonl` (um JSON por linha, schema
  `1.0.0`); timestamp UTC (`timestamp`) + instante relativo do quadro
  (`evidence.features.tempo_video_s`) + `frame_index`. Sem transições, o arquivo
  recebe um registro `sem_achados` (`video/pipeline.py`, emitido após o resumo).
- **Evidência de execução:** B_D_0016 (`config/leito_uti_07.json`) → 10 eventos
  válidos (5 entradas + 5 saídas, todas pareadas por track e área);
  B_N_87 (0 transições) → 1 `sem_achados` válido; ambos validados por
  `contratos.validar.validar_arquivo` com 0 erros.
- **Testes:** `tests/video/test_eventos.py`, `tests/video/test_pipeline_integracao.py`.

### 8. Disponível em API

**Status:** ✔ implementado.

- **Código:** `app/routers/video_areas.py` (`POST /video/areas-criticas`,
  `GET /video/areas-criticas/{job_id}`, `GET /video/areas-criticas/{job_id}/eventos`)
  e `app/areas_jobs.py` (fila em processo, mesmo desenho de `app/pose_jobs.py`).
  O job roda o mesmo `PipelineAreaCritica` da CLI.
- **ROIs por câmera/requisição:** `camera_id` → `config/<camera_id>.json`; o campo
  `areas` (JSON) substitui as ROIs só naquele job.
- **Evidência:** `B_D_0016.mp4` enviado com `camera_id=leito_uti_07` → 360 quadros,
  922 detecções, 10 eventos (5 entradas + 5 saídas), mesmos números da CLI.
- **Testes:** `tests/video/test_api_areas.py` (detector falso: entrada/saída no
  contrato US04, timestamp, ROIs da requisição, `sem_achados`, erros 400/404/422,
  job relido do disco).

## Evidência de execução (últimas rodadas)

| Fonte | Quadros | Detecções | Eventos | Entradas/Saídas | Validação US04 |
|---|---|---|---|---|---|
| `B_D_0016.mp4` (`leito_uti_07`, 2 ROIs) | 360 | 922 | 10 | 5 entradas + 5 saídas (pareadas) | 10 válidos / 0 erros |
| `B_N_87_resized.mp4` (`--video`) | 120 | 216 | 0 | — | 1 registro `sem_achados` (info) válido / 0 erros |

| Evidência de avaliação | Valor registrado |
|---|---|
| COCO val2017 `yolov8n.pt` (medido) | mAP@0.5 = 0,528 · mAP@0.5:0.95 = 0,366 · P = 0,637 · R = 0,487 |
| Validação qualitativa | `saida/video/video_anotado_leito_uti_07.mp4`, `saida/video/rois_leito_uti_07.jpg` |
| Quantitativa própria (ground truth) | `null` — aguarda anotações (item 5); gate `avaliar_us07.py` pronto |

Execução dos comandos em `README.md` e `docs/arquitetura/us07_deteccao_areas_criticas.md`.