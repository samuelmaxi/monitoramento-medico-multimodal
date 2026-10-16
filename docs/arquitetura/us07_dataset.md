# Dataset de detecção da US07

> **US07**: construir o dataset anotável (YOLOv8) a partir dos vídeos de
> `conteudos/videos/`, com rastreabilidade de origem, divisão sem vazamento,
> validação estrutural e gate de aceitação `mAP@0.5 ≥ 0,50`.

**Princípio que guia tudo:** o pipeline **não inventa** anotações, classes nem
métricas. Sem labels revisados por humano, o dataset fica **inapto** e o treino é
**recusado**. O gate só aprova com medição real em ground truth válido.

## Visão geral

```text
conteudos/videos/                 (43 vídeos; 35 canônicos após curadoria)
        │
        │  inventariar          → dataset_us07/metadata/inventario.{csv,json}
        ▼
   frames extraídos             → dataset_us07/frames/<video_id>/frame_*.jpg
        │                       → dataset_us07/metadata/frames_manifest.jsonl
        │  montar (planejar + materializar)
        ▼
dataset_us07/images|labels/{train,val,test}
dataset_us07/data.yaml          (classes = ["person"])
dataset_us07/metadata/divisao.json
dataset_us07/metadata/frames_divisao.jsonl
        │
        │  exportar-anotacao    → anotavel/ (Ultralytics)
        │  importar-anotacoes   ← labels revisados
        ▼
   validar  → dataset_us07/reports/validacao_dataset.json
   verificar-apto  → aptidão + estado do gate (bloqueado até haver medição)
   treinar → runs/detect/us07/
   avaliar → dataset_us07/reports/avaliacao_<split>.json
```

## Layout de saída

```text
dataset_us07/
├── images/{train,val,test}/          # frames materializados (cópia ou symlink)
├── labels/{train,val,test}/          # .txt YOLO (1 linha por caixa)
├── frames/<video_id>/frame_*.jpg     # frames extraídos, com dedup por hash
├── metadata/
│   ├── inventario.csv|json           # SHA-256, duração, resolução, metadados de nome
│   ├── frames_manifest.jsonl         # um registro por frame extraído
│   ├── divisao.json                  # grupo → split + contagens
│   └── frames_divisao.jsonl          # frame → grupo → split (auditoria de vazamento)
├── reports/
│   ├── validacao_dataset.json
│   └── avaliacao_<split>.json
└── data.yaml                         # path: ./dataset_us07 (relativo ao repo)
```

`data.yaml` usa `path: ./dataset_us07`, que a Ultralytics resolve **relativo ao
diretório de execução**. Rode os comandos a partir da raiz do repositório; senão
a Ultralytics cai no `DATASETS_DIR` global e não encontra o dataset.

## Convenção de nomes dos vídeos

O parser (`video/nomes_video.py`) extrai metadados do nome sem transformá-los em
classes. A taxonomia YOLO é **apenas `person`**; cenário/condição/modalidade são
metadados para estratificação e auditoria.

| Token | Significado | Confirmado |
|---|---|---|
| `B` | cama | sim |
| `C` | cadeira | sim |
| `S` | em pé | sim |
| `F` | queda | sim |
| `NF` | sem queda | sim |
| `raw` / `mask` | modalidade do fluxo | sim |
| `_resized` | derivado redimensionado | sim |
| `D` (em `B_D_*`) | — | **não** |
| `N` (em `B_N_*`) | — | **não** |

`N` **não** é interpretado como `NF`. Tokens desconhecidos ficam em
`tokens_desconhecidos`, marcam `requer_revisao=True` e a condição permanece
`None`. Nada é presumido: uma condição só existe quando o nome a declara de forma
inequívoca (`F`/`NF`).

## Deduplicação e agrupamento por origem

- **Vídeo:** SHA-256 de arquivo inteiro. Duplicatas exatas apontam
  `duplicata_de` e são fundidas no mesmo grupo de origem.
- **Frame:** hash perceptual aHash. Frames semelhantes consecutivos são marcados
  `duplicado=True` e não entram no dataset, evitando viés de frames quase iguais.
- **Grupo de origem:** `chave_grupo` remove sufixos de derivação (`raw`/`mask`/
  `resized`). Grupos com o mesmo SHA de vídeo são fundidos. A divisão é feita por
  **grupo**, nunca por frame, para não vazar o mesmo paciente/cena entre splits.

## CLI

Todos os subcomandos compartilham `--destino` (padrão `dataset_us07`).

```bash
uv run python scripts/dataset_us07.py inventariar --videos conteudos/videos
uv run python scripts/dataset_us07.py extrair --intervalo 1.0 --max-frames 30
# com cobertura mínima por vídeo (promove descartes mais diversos):
uv run python scripts/dataset_us07.py extrair --intervalo 0.5 --max-frames 60 \
    --min-frames-por-video 3
uv run python scripts/dataset_us07.py montar --train 0.7 --val 0.2 --test 0.1
uv run python scripts/dataset_us07.py validar
uv run python scripts/dataset_us07.py exportar-anotacao  # padrão: <destino>/anotavel
# ... anotar (Ultralytics) ...
uv run python scripts/dataset_us07.py importar-anotacoes \
    --de anotavel/labels/train --para dataset_us07/labels/train
uv run python scripts/dataset_us07.py verificar-apto
uv run python scripts/dataset_us07.py treinar --modelo yolov8n.pt --epochs 50
uv run python scripts/dataset_us07.py avaliar --pesos runs/detect/us07/weights/best.pt --split test
```

Códigos de retorno: `0` sucesso/apto/gate aprovado; `1` falha, erro estrutural ou
gate reprovado; `2` treino recusado por dataset inapto.

## Aptidão e gate

`verificar_aptidao` exige: `data.yaml` presente, cada split com imagens e ao menos
uma anotação com objetos, e ausência de vazamento de grupo entre splits. Erros
estruturais do validador também bloqueiam.

`avaliar_gate` compara `mAP@0.5` com o mínimo (**0,50** por padrão). Com
`mapa_50=None` o resultado é explicitamente **não medido** e **não aprovado** —
não existe aprovação por omissão.

## Gate de fine-tuning médico (US07 DoD item 5)

Quando o dataset inclui uma classe de `CLASSES_EXIGEM_FINETUNING`, o treino é
seguido automaticamente por avaliação no split `val`. O gate compara a **média
AP@0.5 apenas das classes médicas** com `0,50`; métricas de classes COCO não
podem esconder uma classe clínica abaixo do mínimo. O relatório registra também
o mAP@0.5 global, AP por classe, checkpoint e decisão.

```bash
uv run python scripts/dataset_us07.py --destino dataset_us07 treinar \
    --classes instrumento_cirurgico monitor_multiparametro

uv run python scripts/dataset_us07.py --destino dataset_us07 validar-finetuning \
    --pesos runs/detect/us07/weights/best.pt
```

O arquivo gerado é `dataset_us07/reports/fine_tuning_val.json`. Se uma classe
médica não tiver AP reportada pelo validator, o resultado fica não medido e
reprovado. A taxonomia usada vem de `data.yaml`; uma lista `--classes` divergente
é rejeitada. A meta só pode ser atestada com labels reais no `val`; métricas de
teste não substituem validação.

### Passo a passo operacional: aquisição Roboflow, fusão e validação do gate

Para tentar cumprir a meta do item 5 com os projetos Roboflow `bsbi/medical-equipment-detection` e `anndev/hospitalequipmentdetection`, execute o fluxo abaixo a partir da raiz do repositório. O acesso e a disponibilidade dessas versões dependem da conta Roboflow; não use uma chave publicada anteriormente, rotacione-a primeiro.

1. **Configurar a chave Roboflow no `.env`**:
   Preencha `ROBOFLOW_API_KEY` com sua chave nova/rotacionada. Não a compartilhe nem a versione; `.env` já é ignorado pelo Git.

2. **Baixar os exports locais no formato YOLOv8 via Python**. `--with roboflow` instala o SDK somente para este comando; a chave é lida do `.env` e não é impressa:
   ```bash
   uv run --with roboflow python -c "
   import os
   from dotenv import load_dotenv
   from roboflow import Roboflow

   load_dotenv()
   rf = Roboflow(api_key=os.environ['ROBOFLOW_API_KEY'])
   rf.workspace('bsbi').project('medical-equipment-detection').version(2).download('yolov8', location='downloads/instrumentos')
   rf.workspace('anndev').project('hospitalequipmentdetection').version(7).download('yolov8', location='downloads/monitores')
   "
   ```

3. **Inspecionar as classes exportadas pelos datasets**:
   ```bash
   uv run python scripts/combinar_datasets_roboflow.py inspecionar \
       --dataset inst=downloads/instrumentos \
       --dataset mon=downloads/monitores
   ```

4. **Combinar os datasets remapeando para a taxonomia canônica da US07**:
   Substitua `NOME_EXATO_INSTRUMENTO` e `NOME_EXATO_MONITOR` pelos nomes literais mostrados na inspeção. Confirme visualmente que cada classe corresponde ao objeto de destino; não mapeie classes semelhantes por suposição.
   ```bash
   uv run python scripts/combinar_datasets_roboflow.py combinar \
       --dataset inst=downloads/instrumentos \
       --dataset mon=downloads/monitores \
       --map 'inst:NOME_EXATO_INSTRUMENTO=instrumento_cirurgico' \
       --map 'mon:NOME_EXATO_MONITOR=monitor_multiparametro' \
       --destino dataset_medico
   ```

5. **Validar estrutura e conferir aptidão do dataset gerado**:
   ```bash
   uv run python scripts/dataset_us07.py --destino dataset_medico validar \
       --classes instrumento_cirurgico monitor_multiparametro

   uv run python scripts/dataset_us07.py --destino dataset_medico verificar-apto \
       --classes instrumento_cirurgico monitor_multiparametro \
       --validacao dataset_medico/reports/validacao_dataset.json
   ```

6. **Executar o fine-tuning e avaliar automaticamente o gate (`mAP@0.5 >= 0.50` no `val`)**:
   ```bash
   uv run python scripts/dataset_us07.py --destino dataset_medico treinar \
       --classes instrumento_cirurgico monitor_multiparametro \
       --modelo yolov8n.pt \
       --epochs 50 \
       --project runs/detect \
       --name us07_medico \
       --minimo 0.50
   ```
   O comando treina e valida automaticamente o melhor checkpoint; anote o `save_dir` impresso na saída, pois o Ultralytics pode acrescentar sufixo se o nome já existir.

7. **Conferir a aprovação no relatório oficial**:
   ```bash
   uv run python -m json.tool dataset_medico/reports/fine_tuning_val.json
   ```
   Só está aprovado quando `aprovado` e `gate.aprovado` forem `true`, o split for `val` e a medição existir para cada classe médica. Para reavaliar sem retreinar, use o caminho real de `best.pt` dentro do `save_dir` informado:
   ```bash
   uv run python scripts/dataset_us07.py --destino dataset_medico validar-finetuning \
       --pesos runs/detect/us07_medico/weights/best.pt \
       --classes instrumento_cirurgico monitor_multiparametro \
       --minimo 0.50
   ```

## Critério de acerto IoU ≥ 0,50 (PASCAL VOC / mAP@0.5)

`video/avaliacao_deteccao.py` implementa o critério **sem depender de framework**:
uma detecção é verdadeiro positivo quando casa com uma verdade de terra da mesma
classe e `IoU >= limiar` (padrão **0,50**). O casamento é *greedy* por confiança,
um-para-um e por imagem (`casar_deteccoes`); verdades não casadas viram falsos
negativos. Daí saem TP/FP/FN, precisão/revocação/F1 e a **AP@0.5** por classe
(interpolação em todos os pontos, VOC2010+) e o **mAP@0.5** (`avaliar_deteccao`).
Sem ground truth o `mAP@0.5` é `None` — não medido, nunca fabricado.

```bash
# predições já calculadas (JSONL):
# {"imagem": "B_D_0001__frame_000001.jpg", "class_id": 0, "confianca": 0.9, \
#  "caixa": [x1, y1, x2, y2]}
uv run python scripts/avaliar_voc_us07.py \
    --imagens dataset_us07/images/test --labels dataset_us07/labels/test \
    --predicoes predicoes_test.jsonl \
    --iou 0.5 --minimo 0.5 --output dataset_us07/reports/avaliacao_voc_test.json

# ou gerando as predições direto dos pesos do modelo:
uv run python scripts/avaliar_voc_us07.py \
    --imagens dataset_us07/images/test --labels dataset_us07/labels/test \
    --pesos yolov8n.pt --conf 0.25 --iou 0.5 --minimo 0.5
```

- `--predicoes` e `--pesos` são **mutuamente exclusivos** (uma das duas origens é
  obrigatória); `predicoes_de_detector` roda qualquer detector no diretório.
- `--classes` (ids) filtra a avaliação; por padrão usa a taxonomia do projeto
  (`person` = 0), evitando falsos positivos de classes COCO que o dataset não
  anota.
- `carregar_verdades` lê o ground truth YOLO (`imagens/` + `labels/`).
- `ler_predicoes_jsonl` aceita `caixa: [x1,y1,x2,y2]` ou `x1`/`y1`/`x2`/`y2`.
- Saída: relatório com critério, contagens e `gate` (`avaliar_gate`); exit `0` se
  aprovado, `1` se reprovado ou não medido.
- Testes: `tests/video/test_avaliacao_deteccao.py` e
  `tests/video/test_cli_avaliar_voc.py` (inclui o limite exato de IoU 0,50).

## Integração com a inferência (US07)

O dataset alimenta o treino dos pesos usados por `video/detector.py` e
`video/metricas.py`. O fluxo de inferência de áreas críticas continua descrito em
[`us07_deteccao_areas_criticas.md`](us07_deteccao_areas_criticas.md); a avaliação
de um vídeo usa `scripts/rodar_us07_video.py` e `scripts/avaliar_us07.py`.

## Execução real sobre `conteudos/videos/` (2026-10-08)

Resultados desta rodada (comandos a partir da raiz do repositório). Todos os
números abaixo são medidos, não estimados.

### Inventário (43 arquivos encontrados)

- **43 vídeos** legíveis, **0 com erro de leitura**.
- **38 SHA-256 únicos**; **5 duplicatas exatas**:
  `B_D_0004=B_D_0003`, `B_D_0012=B_D_0011`, `B_D_0018=B_D_0017`,
  `B_D_0020=B_D_0019`, `B_D_0026=B_D_0025`.
- **6 redimensionados**: `B_D_0006..0010_resized`, `B_N_87_resized`.
- **0 arquivos `raw`/`mask`** (a modalidade permanece `desconhecida` para todos).
- Cenários: `cama` (40), `desconhecido` (3). Condições: `desconhecido` (43) —
  ver seção de tokens `D`/`N`.
- Resoluções: 1920x1080 (34), 1100x1080 (6), 1280x720 (2), 2160x4096 (1).
- **3 arquivos fora do domínio** (não seguem a convenção, têm trilha de áudio):
  `Samurai_Desembanha_Espada_Rapidamente.mp4`, `video.mp4`, `video_triste.mp4`.

Artefatos: `dataset_us07/metadata/inventario.{csv,json}` (43 completos) e
`dataset_us07/metadata/inventario_anotacao.{csv,json}` (curado).

### Curadoria para anotação (35 vídeos)

Regra explícita e determinística, aplicada com os módulos de inventário:

1. **Excluir os 3 fora do domínio** (cenário desconhecido).
2. **Excluir as 5 duplicatas exatas**, mantendo a primeira cópia canônica.

Restam **35 vídeos canônicos, 35 SHA-256 únicos, 0 duplicatas**.

### Estratégia de extração

`--intervalo 1.0 --max-frames 30`, deduplicação perceptual aHash ativa
(`--distancia 3`), retomada ativa, sem redimensionamento.

- **121 frames lidos**, **55 marcados como quase-duplicados** (não gravados),
  **66 frames únicos gravados** em `dataset_us07/frames/<video_id>/`.
- Manifesto: `dataset_us07/metadata/frames_manifest.jsonl` (121 registros, com
  marcação `duplicado` e o SHA-256 de cada frame).
- **0 erros de leitura**.

### Divisão e materialização

`montar` estratifica por **(condição, cenário)** e divide **por grupo de origem**
(nunca por frame). Sem avisos nesta rodada.

| Split | Grupos | Frames |
|---|---|---|
| train | 25 | 45 |
| val | 7 | 13 |
| test | 3 | 8 |
| **total** | **35** | **66** |

Artefatos: `dataset_us07/images|labels/{train,val,test}`,
`dataset_us07/metadata/divisao.json`,
`dataset_us07/metadata/frames_divisao.jsonl` e `dataset_us07/data.yaml`.

### Verificação de separação (executada)

- Cada grupo de origem aparece em **exatamente um split**.
- Nenhum `video_id` aparece em mais de um split.
- `verificar_vazamento()` retornou **vazio**; `imagens_repetidas_entre_splits = 0`.
- 0 frames incoerentes entre `divisao.json` e `frames_divisao.jsonl`.
- `data.yaml` carregado pela Ultralytics instalada: `nc=1`, `names={0: person}`.

### Exportação para anotação

`exportar-anotacao --saida anotavel` gerou:

- `anotavel/images/{train,val,test}/` — **66 imagens**;
- `anotavel/labels/{train,val,test}/` — **66 `.txt` vazios** (a preencher);
- `anotavel/pendentes_anotacao.csv` — **66 pendências** (`frame_id`, `split`,
  `imagem`);
- `anotavel/data.yaml` — classes para ferramentas Ultralytics.

**Nenhuma caixa foi criada.** Os `.txt` estão vazios de propósito.

### Correção encontrada na execução real

O nome base do frame (`frame_000001.jpg`) se repetia entre vídeos e a
materialização sobrescrevia arquivos (apenas 8 de 74 frames sobreviviam). Foi
adicionado `video.dataset.nome_imagem_frame(frame_id)`, que prefixa o
`video_id` (`B_D_0001__frame_000001.jpg`), usado tanto em `montar` quanto em
`exportar-anotacao`. Coberto por teste de regressão. Também foi corrigido o uso
de `vars()` em dataclass com `slots` no relatório de validação e o gate passou a
emitir `null` em vez de `NaN` no JSON quando não medido.

## Nomenclatura: tokens `D` e `N` (pendente de confirmação)

Dos 43 nomes reais, os tokens desconhecidos são:

| Token | Ocorrências | Arquivos | Hipótese | Status |
|---|---|---|---|---|
| `D` | 28 | `B_D_0001..0028` | — | **não confirmado** |
| `N` | 12 | `B_N_87,96..106` | — | **não confirmado** |
| `Samurai`/`Desembanha`/`Espada`/`Rapidamente` | 1 cada | clipe extra | fora do domínio | excluído |
| `video`/`triste` | 3 | `video.mp4`, `video_triste.mp4` | fora do domínio | excluído |

`N` **não** é interpretado como `NF`. Como o significado de `D`/`N` é incerto,
**toda condição permanece `desconhecida`** e a estratificação por condição fica
pendente. Isso não impede inventário, extração, divisão por cenário nem a
exportação — só impede separar quedas de não-quedas. A decisão precisa vir do
responsável pelos dados (ex.: confirmar se `D`=queda e `N`=sem queda, ou o
contrário).

## Guia: anotar e importar as anotações

Anotação (o repositório **não** gera caixas):

1. Abra as imagens de `anotavel/images/train` (e as demais por split) em sua
   ferramenta de anotação preferida.
2. Crie o label de objeto **`person`** (índice `0`, igual a `data.yaml`).
3. Desenhe **uma caixa por pessoa** em cada frame. Se não houver pessoa, salve a
   **imagem vazia** (negativa válida). Não invente caixas e não derive a caixa do
   token `F`/`B`.
4. Exporte os labels em **formato YOLO (Ultralytics 1.1)** — caixas normalizadas
   `class_id x_center y_center width height` — e importe de volta:

   ```bash
   uv run python scripts/dataset_us07.py --destino dataset_us07 importar-anotacoes \
       --de /caminho/export --para dataset_us07/labels/train
   ```

   Repita por split. `importar-anotacoes` copia apenas os `.txt` de label válidos
   e ignora `data.txt`.

### Validar, treinar e avaliar

Depois de anotar os três splits:

```bash
uv run python scripts/dataset_us07.py --destino dataset_us07 validar
uv run python scripts/dataset_us07.py --destino dataset_us07 verificar-apto
uv run python scripts/dataset_us07.py --destino dataset_us07 treinar \
    --modelo yolov8n.pt --epochs 50
uv run python scripts/dataset_us07.py --destino dataset_us07 avaliar \
    --pesos runs/detect/us07/weights/best.pt --split test
```

O gate `mAP@0.5 ≥ 0,50` fica **bloqueado** até existir avaliação real em ground
truth. Exporte os labels em formato YOLO com `person` no índice `0`; caso
contrário o `validar` acusa `class_id fora do intervalo`.

## Limitações conhecidas

- Sem anotações humanas, o dataset é estruturalmente válido mas **inapto** para
  treino supervisionado (66 imagens, 0 objetos anotados).
- **Os 35 vídeos canônicos têm condição desconhecida** (`D`/`N` não confirmados);
  a estratificação por queda/não-queda só será possível após revisão humana.
  Hoje a divisão é estratificada apenas por cenário (`cama`).
- **Poucos frames por vídeo** (1–3) por causa da deduplicação aHash; se a
  validação mostrar pouca variabilidade, reduza `--intervalo` (ex.: `0.5`) e
  rode `extrair` novamente — a retomada evita retrabalho.
- `data.yaml` depende do diretório de execução (comportamento da Ultralytics).
- A revisão visual das anotações é humana; o validador só cobre consistência
  estrutural.
- A separação protege contra vazamento por **SHA-256 de vídeo** e por
  `chave_grupo` (sufixos `raw`/`mask`/`resized`), mas não detecta derivados que
  sejam reencodados com bytes diferentes e sem sufixo de derivação no nome.

