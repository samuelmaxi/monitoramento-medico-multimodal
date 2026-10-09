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
- **Métricas quantitativas não são fabricadas**: sem dataset anotado não há
  cálculo de mAP; o script `avaliar_us07.py` produz as métricas quando recebe um
  `data.yaml` YOLO. Sem fine-tuning, o relatório registra apenas o **baseline
  COCO** do model card oficial (`mAP@0.5:0.95 = 0,373` para `yolov8n.pt`, com URL
  de referência) e a validação qualitativa; `mAP@0.5`/Precision/Recall ficam
  `null` porque a fonte oficial não os publica — **não são estimados**.

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

**Status:** ✔ implementado (com exemplo por câmera em `config/leito_uti_07.json`).

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

- **Código:** `video/detector.py::_inferir` envia `iou` ao Ultralytics
  (`argumentos["iou"]`); valor default em `video/detector.py` (`ConfiguracaoDetector`).
- **Config:** `config/exemplo_us07.json` → `detector.iou: 0.5`.
- **Justificativa:** mesmo limiar de IoU da métrica `mAP@0.5` (PASCAL VOC)
  garante coerência entre detecção e avaliação.
- **Teste:** `tests/video/test_detector.py` (assert de `argumentos["iou"]`).

### 4. Limiar de confiança documentado (padrão Ultralytics conf = 0,25)

**Status:** ✔ implementado e documentado.

- **Código/config:** `config/exemplo_us07.json` → `detector.confianca: 0.25`;
  default em `video/detector.py`.
- **Docs:** `README.md` e `docs/arquitetura/us07_deteccao_areas_criticas.md`
  ("Decisões e parâmetros").
- **Justificativa:** `0.25` é o default Ultralytics; manter o default facilita
  comparar com a literatura e com o validator.

### 5. Fine-tuning em classes médicas com mAP@0.5 ≥ 0,50 no val

**Status:** ➖ pendente — gate bloqueado até haver anotações; braço baseline adotado.

- **Rota (pronta):** `scripts/dataset_us07.py` (`montar` → `validar` →
  `verificar-apto` → `treinar` → `avaliar`) constrói o dataset sem vazamento,
  exige aptidão e aplica o gate `mAP@0.5 ≥ 0,50` (`video/dataset.py::avaliar_gate`).
  Com 0 labels anotadas o gate fica **bloqueado** (`medido=false`) — nunca
  aprovado por omissão (`docs/arquitetura/us07_dataset.md`, 2026-10-08).
- **Comportamento atual:** checkpoint pré-treinado COCO (`yolov8n.pt`) + baseline
  e validação qualitativa registrados no relatório (alternativa prevista no DoD).

### 6. Métricas mAP@0.5, mAP@0.5:0.95, Precision e Recall registradas no relatório

**Status:** ✔ registradas no relatório — braço baseline COCO (quantitativa própria depende do dataset).

- **Quantitativa real (dataset anotado):** `scripts/avaliar_us07.py` roda o
  validator Ultralytics e grava `mAP@0.5`, `mAP@0.5:0.95`, precision, recall e
  metadados; o gate salva `dataset_us07/reports/avaliacao_<split>.json`.
- **Braço sem fine-tuning (vigente):** a seção `avaliacao` do
  `relatorio_us07.json` registra `metricas_coco_baseline()` (`video/metricas.py`):
  `mAP@0.5:0.95 = 0.373` (valor **oficial do model card**, com URL de referência);
  `mAP@0.5`/Precision/Recall ficam `null` porque **a página oficial não os
  publica** — não são estimados para não fabricar métrica. `quantitativa_em_ground_truth`
  fica `null` e `qualitativa.videos_anotados` lista os vídeos da validação
  qualitativa (`GravadorVideo`, quando `visualizacao.ativa`).
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
- **Evidência de execução:** B_D_0016 → 6 eventos válidos (1 entrada + 5 saídas);
  B_N_87 (0 transições) → 1 `sem_achados` válido; ambos validados por
  `contratos.validar.validar_arquivo` com 0 erros.
- **Testes:** `tests/video/test_eventos.py`, `tests/video/test_pipeline_integracao.py`.

## Evidência de execução (últimas rodadas)

| Fonte | Quadros | Detecções | Eventos | Entradas/Saídas | Validação US04 |
|---|---|---|---|---|---|
| `B_D_0016.mp4` (config exemplo) | 360 | 922 | 6 | 1 entrada + 5 saídas | 6 válidos / 0 erros |
| `B_N_87_resized.mp4` (`--video`) | 120 | 216 | 0 | — | 1 registro `sem_achados` (info) válido / 0 erros |

| Evidência de avaliação | Valor registrado |
|---|---|
| Baseline COCO `yolov8n.pt` (model card) | `mAP@0.5:0.95 = 0.373`; `mAP@0.5`/**P**/**R** = `null` (não publicados; não estimados) |
| Quantitativa própria (ground truth) | `null` — aguarda anotações (item 5); gate `avaliar_us07.py` pronto |

Execução dos comandos em `README.md` e `docs/arquitetura/us07_deteccao_areas_criticas.md`.