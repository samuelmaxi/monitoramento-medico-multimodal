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
  `data.yaml` YOLO.

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

**Status:** ➖ pendente (não há fine-tuning nem dataset médico anotado).

- **Código/rota:** quando existir dataset YOLO com `data.yaml` e split
  val/teste, `scripts/avaliar_us07.py` valida e reporta as métricas. Não são
  fabricadas métricas sem dataset.
- **Comportamento atual:** usa o checkpoint pré-treinado COCO (`yolov8n.pt`) com
  validação qualitativa (menu do DoD permite a alternativa sem fine-tuning).

### 6. Métricas mAP@0.5, mAP@0.5:0.95, Precision e Recall registradas no relatório

**Status:** ✔ implementado (tooling) — execução depende de dataset.

- **Código:** `scripts/avaliar_us07.py` roda o validator Ultralytics e grava
  `saida/video/metricas.json` com `mAP@0.5`, `mAP@0.5:0.95`, precision, recall e
  metadados (pesos, versão do Ultralytics, caminho do dataset).
- **Sem dataset:** sem surgirem "métricas" sintéticas; o relatório documenta a
  alternativa qualitativa (DoD permite).
- **Teste:** `tests/video/test_metricas.py` (validator simulado).

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

Execução dos comandos em `README.md` e `docs/arquitetura/us07_deteccao_areas_criticas.md`.