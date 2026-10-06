# US07 — detecção em vídeo e transições em áreas críticas

## Objetivo e fluxo

A US07 lê quadros de vídeo com OpenCV, detecta e rastreia objetos com Ultralytics YOLO, avalia a contenção das caixas delimitadoras em regiões de interesse (ROIs) poligonais, acompanha transições entre dentro/fora e publica eventos `entrada_area_critica` e `saida_area_critica` pelo contrato `EventoAchado` da US04. O módulo fica em `video/`; ingestão, geometria, monitor, tradução de eventos e orquestração são camadas separadas, testáveis sem pesos ou GPU.

```text
arquivo de vídeo → OpenCV → YOLO/ByteTrack → contenção ROI → estado por track → EventoAchado US04 → JSONL
```

A configuração de exemplo está em `config/exemplo_us07.json`. Execução:

```bash
uv run python scripts/rodar_us07_video.py config/exemplo_us07.json
uv run python scripts/rodar_us07_video.py config/exemplo_us07.json --max-frames 30
```

A primeira execução pode baixar `yolov8n.pt` pelo Ultralytics. `--max-frames` é útil para validar instalação e configuração. O JSON de resumo apresenta quadros, detecções, eventos, classes, resolução e duração. A visualização anotada é opcional.

## Decisões e parâmetros

- **Modelo inicial:** `yolov8n.pt`, checkpoint COCO leve. A lista de classes vem da configuração e detecções fora dela são filtradas. COCO reconhece `person`, `bed`, `chair`, `couch` e `bottle`, mas não classes clínicas como instrumentos cirúrgicos; elas não podem ser inferidas com precisão sem dataset especializado e fine-tuning.
- **Limiares de inferência:** confiança `0.25`, IoU de NMS `0.50`, tamanho de entrada `640` e rastreamento persistente ByteTrack. O tracking mantém IDs entre quadros para separar indivíduos e preservar estado por objeto.
- **ROIs:** polígonos são especificados em coordenadas de uma resolução de referência e escalados para o vídeo. O critério padrão exige ao menos 50% da área da caixa dentro do polígono; o cálculo de interseção usa clipping poligonal. Também existe modo baseado no centro da caixa.
- **Transições:** o monitor aplica persistência configurável a entradas/saídas e tolerância a oclusões breves; entrada e saída do mesmo track são emitidas como achados separados. O início de observação não é tratado como entrada, salvo opção explícita.
- **Contrato/privacidade:** eventos incluem modalidade `video`, timestamps UTC, tempo relativo do quadro em evidência e `patient_id` pseudonimizado `pt_<24 hex>`. IDs diretos são recusados pela configuração.
- **Severidade:** `baixa` por padrão; a transição informa estado, não representa diagnóstico nem alerta clínico por si só.

## Avaliação

O vídeo de demonstração configura a ROI e exercita a inferência real, sendo adequado para verificação qualitativa do pipeline. Sem anotações de referência (ground truth), não é válido atribuir mAP, precisão ou recall. Para avaliação quantitativa, use um dataset YOLO rotulado com `data.yaml` e split de validação/teste:

```bash
uv run python scripts/avaliar_us07.py datasets/areas/data.yaml \
  --weights yolov8n.pt --split val --output saida/video/metricas.json
```

O script usa o validator Ultralytics e reporta `mAP@0.5`, `mAP@0.5:0.95`, precision e recall, junto dos pesos, versão do Ultralytics e caminho do conjunto. Não sintetiza métricas quando o dataset está ausente.

## Testes e limitações

Os testes em `tests/video/` cobrem geometria, ROIs, estados, parsing do detector, leitor OpenCV, contrato, integração ponta a ponta com vídeo sintético, métricas (validator simulado) e CLI. Rode `uv run pytest tests/video -q` e `uv run ruff check video tests/video scripts/rodar_us07_video.py scripts/avaliar_us07.py`.

A saída depende da qualidade da gravação, da calibração do polígono e das classes suportadas pelo checkpoint. O exemplo de vídeo demonstra execução do pipeline, não desempenho clínico ou adequação para uso assistencial; não é dispositivo médico validado.
