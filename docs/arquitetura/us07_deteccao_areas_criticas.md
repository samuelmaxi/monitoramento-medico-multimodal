# US07 — detecção em vídeo e transições em áreas críticas

A rastreabilidade item a item ao *Definition of Done* da tarefa (evidência em
código, config, status e justificativas) está em
[`docs/relatorio_us07_dod.md`](../relatorio_us07_dod.md).

## Objetivo e fluxo

A US07 lê quadros de vídeo com OpenCV, detecta e rastreia objetos com Ultralytics YOLO, avalia a contenção das caixas delimitadoras em regiões de interesse (ROIs) poligonais, acompanha transições entre dentro/fora e publica eventos `entrada_area_critica` e `saida_area_critica` pelo contrato `EventoAchado` da US04. O módulo fica em `video/`; ingestão, geometria, monitor, tradução de eventos e orquestração são camadas separadas, testáveis sem pesos ou GPU.

```text
arquivo de vídeo → OpenCV → YOLO/ByteTrack → contenção ROI → estado por track → EventoAchado US04 → JSONL
```

A configuração de exemplo está em `config/exemplo_us07.json`. Execução:

```bash
uv run python scripts/rodar_us07_video.py config/exemplo_us07.json
uv run python scripts/rodar_us07_video.py config/exemplo_us07.json --max-frames 100
```

O exemplo usa `conteudos/videos/B_D_0016.mp4` (3 s a 120 fps): uma pessoa anda da
direita para a esquerda e cruza a área crítica `lateral_leito` (faixa vertical à
esquerda do leito), gerando `entrada_area_critica` e `saida_area_critica`. O critério
de contenção é `centro` (menos sensível a ruído de caixa do que fração de área) e a
persistência tolera oito quadros sem detecção. A primeira execução pode baixar
`yolov8n.pt` pelo Ultralytics. `--max-frames` é útil para validar instalação e
configuração. O JSON de resumo apresenta quadros, detecções, eventos, classes,
resolução e duração. A visualização anotada é opcional.

### Geração de config por vídeo

Para criar um config dedicado por câmera/vídeo (reaproveitando ROI e parâmetros), use o gerador:

```bash
uv run python scripts/gerar_config_us07.py \
  --video conteudos/videos/B_D_0003.mp4 \
  --template config/exemplo_us07.json \
  --saida config/B_D_0003.json
```

Ele copia o template, define `fonte.id` (stem do vídeo), rebaseia caminhos, preenche `fonte.fps` (OpenCV) e valida o resultado antes de gravar. Flags: `--forcar`, `--sem-metadados`, `--patient-id`, `--id`.

### Lote e vídeo único

O mesmo script aceita `--video` (um arquivo) e `--videos-dir` (todos os vídeos
`mp4/avi/mov/mkv` de uma pasta):

```bash
uv run python scripts/rodar_us07_video.py config/exemplo_us07.json \
  --video conteudos/videos/B_D_0001.mp4
uv run python scripts/rodar_us07_video.py config/B_D_0003.json \
  --video conteudos/videos/B_D_0003.mp4  # ou direto pelo config gerado
uv run python scripts/rodar_us07_video.py config/exemplo_us07.json \
  --videos-dir conteudos/videos
```

- Nos dois modos o `fonte.video` é sobrescrito e o `source_id` de cada evento vira
  o nome (stem) do arquivo; `patient_id`, `bed_id` e `proc` seguem da configuração.
- Os eventos vão para `eventos_<stem>.jsonl` no diretório de saída da configuração
  (`saida/video/`), recriado a cada execução — reproduzível. **Todo vídeo processado
  gera o seu JSONL**: quando não há evento de entrada/saída, o pipeline emite um
  registro `sem_achados` (severidade `info`) com o resumo do processo (quadros
  lidos, detecções, duração, classes) deixando explícito que não houve queda ou
  saída; vídeo com falha não gera JSONL (fica no relatório).
- O detector YOLO é carregado uma única vez para o lote e o tracker é reiniciado
  entre vídeos (via `DetectorYolo.reiniciar_rastreamento`), evitando que o estado do
  ByteTrack vaze de um vídeo para o outro.
- Um vídeo com falha é pulado com log de erro e o lote continua; o exit code final
  é `1` se alguma falha ocorreu. O relatório consolidado (`videos_processados`,
  `videos_com_falha`, resumos por vídeo) é impresso no console e gravado em
  `saida/video/relatorio_us07.json` (mude o destino com `--saida-relatorio`).
- No modo "vídeo da configuração" o JSONL configurado (`eventos.caminho_jsonl`) é
  recriado a cada execução, no mesmo espírito reproduzível do lote.

## Decisões e parâmetros

- **Modelo inicial:** `yolov8n.pt`, checkpoint COCO leve. A lista de classes vem da configuração e detecções fora dela são filtradas. COCO reconhece `person`, `bed`, `chair`, `couch` e `bottle`, mas não classes clínicas como instrumentos cirúrgicos; elas não podem ser inferidas com precisão sem dataset especializado e fine-tuning.
- **Limiares de inferência:** confiança `0.25`, IoU de NMS `0.50`, tamanho de entrada `640` e rastreamento persistente ByteTrack. O tracking mantém IDs entre quadros para separar indivíduos e preservar estado por objeto.
- **ROIs:** polígonos são especificados em coordenadas de uma resolução de referência e escalados para o vídeo (`video/areas.py::para_escala`). O critério padrão exige ao menos 50% da área da caixa dentro do polígono; o cálculo de interseção usa clipping poligonal. Também existe modo baseado no centro da caixa, usado pelo exemplo (`centro`) por ser mais estável a caixas que oscilam de tamanho. As ROIs são configuráveis por vídeo/câmera: cada arquivo em `config/` representa uma câmera. `config/leito_uti_07.json` tem duas ROIs: `lateral_leito` (faixa retangular) e `borda_leito` (trapézio que acompanha a borda do colchão por onde o paciente sai da cama — risco de queda); a prévia desenhada fica em `saida/video/rois_leito_uti_07.jpg`. Campo cirúrgico segue o mesmo formato (polígono + `classe_filtro`), mas não há vídeo cirúrgico no acervo para calibrá-lo.
- **Transições:** o monitor aplica persistência configurável a entradas/saídas e tolerância a oclusões breves; entrada e saída do mesmo track são emitidas como achados separados. O início de observação não é tratado como entrada, salvo opção explícita (`primeira_observacao_entra`). **Entrada e saída são pareadas:** só há `saida_area_critica` de um track que teve `entrada_area_critica` emitida. Um track visto pela primeira vez já dentro da área — quem já estava lá no início do vídeo, ou um track novo que o ByteTrack criou ao fragmentar a pessoa em vídeo de 120 fps — não gera nem entrada nem saída; para registrar ambos, ligue `primeira_observacao_entra`. Um objeto que some por mais de `persistencia_quadros` passa a contar como fora, sem perder o estado, então se reaparece dentro gera nova entrada.
- **Contrato/privacidade:** eventos incluem modalidade `video`, timestamps UTC, tempo relativo do quadro em evidência e `patient_id` pseudonimizado `pt_<24 hex>`. IDs diretos são recusados pela configuração.
- **Severidade:** `baixa` por padrão; a transição informa estado, não representa diagnóstico nem alerta clínico por si só.

## Avaliação

O vídeo de demonstração configura a ROI e exercita a inferência real, sendo adequado para verificação qualitativa do pipeline. Sem anotações de referência (ground truth), não é válido atribuir mAP, precisão ou recall. Para avaliação quantitativa, use um dataset YOLO rotulado com `data.yaml` e split de validação/teste:

```bash
uv run python scripts/avaliar_us07.py datasets/areas/data.yaml \
  --weights yolov8n.pt --split val --output saida/video/metricas.json
```

O script usa o validator Ultralytics e reporta `mAP@0.5`, `mAP@0.5:0.95`, precision e recall, junto dos pesos, versão do Ultralytics e caminho do conjunto. Não sintetiza métricas quando o dataset está ausente.

### Braço sem fine-tuning: baseline COCO + validação qualitativa

Até existir dataset clínico anotado, o relatório registra as métricas do
`yolov8n.pt` **medidas no COCO val2017** e a **validação qualitativa**:

```bash
uv run python scripts/avaliar_us07.py --baseline --output saida/video/metricas_baseline.json
```

- `metricas_coco_baseline()` (`video/metricas.py`) devolve as quatro métricas
  medidas por `scripts/avaliar_us07.py` no COCO val2017 (5000 imagens):
  mAP@0.5 = 0,528, mAP@0.5:0.95 = 0,366, Precision = 0,637, Recall = 0,487. O
  model card publica só mAP@0.5:0.95 = 0,373; a pequena diferença vem do IoU de
  NMS 0,5 do projeto (o Ultralytics usa 0,7 na validação). Resultado completo,
  com AP@0.5 por classe, em `saida/video/metricas_coco_val2017.json`.
- A seção `avaliacao` do `relatorio_us07.json` contém esse baseline, os vídeos
  anotados da validação qualitativa (`GravadorVideo`, quando
  `visualizacao.ativa`) e `quantitativa_em_ground_truth = null` até existir
  avaliação real.

## Testes e limitações

Os testes em `tests/video/` cobrem geometria, ROIs, estados, parsing do detector, leitor OpenCV, contrato, integração ponta a ponta com vídeo sintético, métricas (validator simulado) e CLI. Rode `uv run pytest tests/video -q` e `uv run ruff check video tests/video scripts/rodar_us07_video.py scripts/avaliar_us07.py`.

A saída depende da qualidade da gravação, da calibração do polígono e das classes suportadas pelo checkpoint. O exemplo de vídeo demonstra execução do pipeline, não desempenho clínico ou adequação para uso assistencial; não é dispositivo médico validado.
