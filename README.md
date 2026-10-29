# monitoramento-medico-multimodal

Projeto de monitoramento médico multimodal. Nesta fase inicial, o foco está na
obtenção e organização dos conteúdos multimodais (vídeos, áudio, documentos,
anomalias e fusão de alertas) que serão utilizados como fonte de dados do
sistema.

## Estrutura do projeto

```
.
├── app/                        # API FastAPI que chama Azure Speech/Language
│   ├── main.py                 # Cria o app, carrega .env, inclui routers, GET /health
│   ├── config.py                # Leitura de variáveis Azure (Speech, Language, Storage)
│   ├── azure_clients.py          # Clientes Azure SDK (Speech, Language, Blob Storage)
│   ├── errors.py                 # Tradução de erros Azure em HTTPException (401/403/429)
│   ├── schemas.py                 # Modelos Pydantic de request/response
│   ├── pose_jobs.py               # Fila em processo dos jobs de pose (US06)
│   ├── areas_jobs.py              # Fila em processo dos jobs de áreas críticas (US07)
│   └── routers/
│       ├── transcription.py       # POST /transcription (síncrono, US11)
│       ├── text_analysis.py       # POST /text/analysis (US13)
│       ├── audio_analysis.py      # POST /audio/analysis (áudio → US11 → US13)
│       ├── sentiment.py            # POST /sentiment
│       ├── sinais_vitais.py        # POST /sinais-vitais/news2, POST /sinais-vitais/medicoes (US15)
│       └── video_pose.py           # POST /video/pose, GET /video/pose/{job_id}[/keypoints|/angles]
├── sinais/                    # Anomalias em sinais vitais: NEWS2 + baseline do paciente (US15)
├── dados/                     # MIMIC-IV Demo baixado por script (gitignored)
├── tests/
│   ├── conftest.py               # Fixtures pytest (TestClient, skip sem Azure configurada)
│   ├── test_smoke.py              # Smoke tests reais contra Azure (transcrição + sentiment)
│   └── fixtures/                  # Áudio de exemplo para os testes (gitignored)
├── conteudos/                 # Conteúdos baixados do Google Drive (gitignored)
├── scripts/
│   ├── get_content_script.py  # Download dos conteúdos do Google Drive
│   ├── download_openpose_models.sh  # Pesos do OpenPose com checksum (US06)
│   ├── baixar_sinais.py       # Download do MIMIC-IV Demo (US15)
│   ├── demo_us15.py           # Roda e confere cada item da DoD da US15
│   ├── rodar_us15.py          # Replay em tempo real dos sinais vitais (US15)
│   └── avaliar_us15.py        # Avaliação com anomalias injetadas (US15)
├── video/
│   └── pose/                  # Análise postural com OpenPose (US06)
├── teste/
│   └── conteudos/             # Estrutura de destino dos conteúdos
├── terraform/                 # Infraestrutura Azure (Speech, Language, Blob Storage)
│   ├── versions.tf
│   ├── providers.tf
│   ├── variables.tf
│   ├── iam.tf                 # Recurso de grupo, storage account, cognitive services
│   ├── outputs.tf
│   └── terraform.tfvars.example
├── main.py                    # Ponto de entrada básico (verificação)
├── pyproject.toml             # Metadados e dependências do projeto
├── uv.lock                    # Lockfile de dependências (uv)
├── .env.sample                # Template das variáveis de ambiente
└── .python-version            # Versão do Python utilizada
```

## Pré-requisitos

- Python `>= 3.13` (consulte `.python-version`)
- [uv](https://docs.astral.sh/uv/) como gerenciador de pacotes e ambientes
- Acesso à internet e permissão de leitura na pasta do Google Drive
- Uma conta/arquivo `.env` configurado (veja [Variáveis de ambiente](#variáveis-de-ambiente))

## Instalação

1. Clone o repositório e entre na pasta do projeto.
2. Crie o arquivo de ambiente a partir do template:

   ```bash
   cp .env.sample .env
   ```

3. Instale as dependências com o `uv`:

   ```bash
   uv sync
   ```

   Será criado o ambiente virtual `.venv` e instaladas as dependências
   listadas no `uv.lock` (`gdown` e `python-dotenv`).

### Instalação alternativa (sem `uv`)

```bash
python -m venv .venv
source .venv/bin/activate
pip install "gdown>=6.4.0" "python-dotenv>=1.2.3"
```

## Variáveis de ambiente

| Variável                          | Obrigatória                      | Descrição                                                                                                                    | Exemplo                                                                                |
| --------------------------------- | -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `GOOGLE_DRIVE_FOLDER_URL`         | Sim                              | URL da pasta do Google Drive cujo conteúdo será baixado para `conteudos/`. O script aborta com erro se não estiver definida. | `https://drive.google.com/drive/folders/1sJN538ANpeud1JzqPMjoNXK_CDXDTo3M?usp=sharing` |
| `AZURE_SPEECH_KEY`                | Sim (para transcrição)           | Chave da API do serviço Azure Speech. Obtida via `terraform output speech_key`.                                              | `a1b2c3d4e5f6...`                                                                      |
| `AZURE_SPEECH_REGION`             | Sim (para transcrição)           | Região do serviço Azure Speech. Fixada em `eastus` para usar tier gratuita.                                                  | `eastus`                                                                               |
| `AZURE_LANGUAGE_ENDPOINT`         | Sim (sentimento e US13)          | Endpoint do serviço Azure AI Language. Obtido via `terraform output language_endpoint`.                                      | `https://monitoramento-language-dev.cognitiveservices.azure.com/`                      |
| `AZURE_LANGUAGE_KEY`              | Sim (sentimento e US13)          | Chave da API do serviço Azure Language. Obtida via `terraform output language_key`.                                          | `a1b2c3d4e5f6...`                                                                      |
| `AZURE_SPEECH_ENDPOINT`           | Não                              | Endpoint do recurso Speech. Padrão: `https://<AZURE_SPEECH_REGION>.api.cognitive.microsoft.com`.                             | `https://spc-tc4-multimodal.cognitiveservices.azure.com`                               |
| `TRANSCRIPTION_OUTPUT_DIR`        | Não                              | Pasta das transcrições (JSON e áudio de origem). Padrão: `saida/transcricao`.                                                | `saida/transcricao`                                                                    |
| `AZURE_STORAGE_CONNECTION_STRING` | Não (não usada pela transcrição) | Connection string do Azure Blob Storage. Não é mais usada pela transcrição (US11), que envia o áudio direto na requisição.  | `DefaultEndpointsProtocol=https;AccountName=...`                                       |
| `AZURE_BLOB_CONTAINER_NAME`       | Não                              | Nome do container Blob Storage para arquivos de áudio. Padrão: `audio-transcripts`.                                          | `audio-transcripts`                                                                    |
| `PSEUDONYM_KEY`                   | Só para enviar IDs de origem     | Chave HMAC (≥ 32 bytes) de `contratos/pseudonimizacao.py`. Usada por `POST /video/pose` e `POST /sinais-vitais/medicoes` quando `patient_id`/`encounter_id` não chegam já pseudonimizados, e pelos scripts da US15. | `python -c "import secrets; print(secrets.token_hex(32))"`                             |
| `POSE_CONFIG_PATH`                | Não                              | `config.yaml` usado pela API de pose. Padrão: `video/pose/config.yaml`.                                                      | `video/pose/config.yaml`                                                               |
| `VIDEO_MAX_UPLOAD_MB`             | Não                              | Tamanho máximo do vídeo aceito por `POST /video/pose` e `POST /video/areas-criticas`. Padrão: `500`.                         | `500`                                                                                  |
| `US07_CONFIG_PATH`                | Não                              | Config da US07 usada pela API quando o `POST` não informa `camera_id`. Padrão: `config/exemplo_us07.json`.                   | `config/exemplo_us07.json`                                                             |
| `US07_CONFIG_DIR`                 | Não                              | Pasta das configs por câmera: `camera_id=X` lê `<US07_CONFIG_DIR>/X.json`. Padrão: `config`.                                 | `config`                                                                               |
| `US07_OUTPUT_DIR`                 | Não                              | Onde a API da US07 grava `eventos.jsonl` e `job.json` de cada job. Padrão: `saida/video/api`.                                | `saida/video/api`                                                                      |

O template pronto pode ser copiado de `.env.sample`:

```
GOOGLE_DRIVE_FOLDER_URL=https://drive.google.com/drive/folders/SEU_ID_AQUI?usp=sharing

AZURE_SPEECH_KEY=
AZURE_SPEECH_REGION=eastus
AZURE_LANGUAGE_ENDPOINT=
AZURE_LANGUAGE_KEY=
AZURE_STORAGE_CONNECTION_STRING=
AZURE_BLOB_CONTAINER_NAME=audio-transcripts

PSEUDONYM_KEY=
POSE_CONFIG_PATH=video/pose/config.yaml
VIDEO_MAX_UPLOAD_MB=500
```

## Como executar cada módulo

Os comandos devem ser executados a partir da raiz do projeto.

### `main.py` — verificação básica

```bash
python main.py
```

Exibe `Hello World`, servindo apenas como sane-check da instalação.

### `scripts/get_content_script.py` — download dos conteúdos

```bash
python scripts/get_content_script.py
```

- Lê `GOOGLE_DRIVE_FOLDER_URL` do `.env`.
- Baixa o conteúdo da pasta do Google Drive para `conteudos/` usando `gdown --folder`.
- O `gdown` cria uma subpasta com o nome da pasta remota, por exemplo
  `conteudos/conteudos/videos/`.

### US07 — detecção de objetos e áreas críticas em vídeo

O pipeline aplica `YOLOv8` (Ultralytics) a vídeos, avalia áreas críticas (ROIs) em
formato de polígonos configuráveis por câmera e emite eventos `entrada_area_critica`
e `saida_area_critica` no schema da US04. Os dados de saída são JSONL e relatórios
compatíveis com a validação do projeto.

#### 1. Preparação rápida

```bash
uv sync
```

Crie/edite o arquivo `.env` se for baixar vídeos via `scripts/get_content_script.py`.

#### 1b. Gerar config por vídeo (recomendado)

O script `scripts/gerar_config_us07.py` copia um template e ajusta apenas o vídeo, o `fonte.id` e os caminhos de saída.

```bash
uv run python scripts/gerar_config_us07.py \
  --video conteudos/videos/B_D_0003.mp4 \
  --template config/exemplo_us07.json \
  --saida config/B_D_0003.json
```

Isso gera `config/B_D_0003.json` com as mesmas classes/ROIs do template, o FPS do vídeo preenchido (via OpenCV) e o JSONL em `saida/video/eventos_B_D_0003.jsonl`. Use `--forcar` para sobrescrever, `--sem-metadados` para não abrir o vídeo e `--patient-id` para trocar o pseudônimo.

#### 2. Processar vídeo da configuração (padrão)

O arquivo `config/exemplo_us07.json` define: vídeo, pesos `yolov8n.pt`, classes
`["person","bed","chair","couch","bottle"]`, ROI `lateral_leito` (polígono),
`conf=0.25`, `iou=0.5`, rastreamento `ByteTrack` e caminho para `eventos_us07.jsonl`.

Para fazer um teste rápido:

```bash
uv run python scripts/rodar_us07_video.py config/exemplo_us07.json --max-frames 100
```

Para processar o vídeo por inteiro:

```bash
uv run python scripts/rodar_us07_video.py config/exemplo_us07.json
```

Esse modo gera:

- `saida/video/eventos_<stem>.jsonl` — eventos US04 do vídeo (recriado a cada execução).
- `saida/video/relatorio_<stem>.json` — resumo único: quadros, detecções, classes, eventos e seção `avaliacao` (as quatro métricas do `yolov8n.pt` medidas no COCO val2017 e a lista de vídeos anotados).

Exemplo de validação:

```bash
python - <<'PY'
import json

r = json.load(open("saida/video/relatorio_B_D_0016.json"))
print("vídeo:", r["video"])
print("quadros:", r["quadros_lidos"], "| detecções:", r["deteccoes_total"])
print("eventos:", r["eventos"], "(entrada/saída:", r["entradas"], "/", r["saidas"], ")")
print("classes:", r["classes_detectadas"])
print("avaliacao:", r["avaliacao"]["metodo"], "| mAP@0.5:0.95:", r["avaliacao"]["metricas"]["mAP@0.5:0.95"])
PY
```

Caso não haja transições, o JSONL registra um único `sem_achados` (severidade `info`).

#### 3. Processar um vídeo específico (dinâmico)

Sobrescreve `fonte.video` e usa o nome do arquivo como `source_id`:

```bash
uv run python scripts/rodar_us07_video.py config/exemplo_us07.json \
  --video conteudos/videos/B_D_0002.mp4
```

Cria:

```text
saida/video/eventos_B_D_0002.jsonl
saida/video/relatorio_B_D_0002.json
```

#### 4. Processar todos os vídeos de uma pasta (lote)

```bash
uv run python scripts/rodar_us07_video.py config/exemplo_us07.json \
  --videos-dir conteudos/videos \
  --saida-relatorio saida/video/relatorio_todos.json
```

Cria:

- `saida/video/eventos_<stem>.jsonl` para cada vídeo processado com sucesso
- `saida/video/relatorio_<stem>.json` para cada vídeo (para inspeção por arquivo)
- `saida/video/relatorio_todos.json` — relatório consolidado (`videos_processados`, `videos_com_falha`, `eventos_totais`, `resumos`, `falhas`, `avaliacao`)

Ao usar `--max-frames` é ideal para validação rápida. Vídeos com falha não geram
seu JSONL e ficam registrados em `falhas` (exit code `1`). O detector YOLO é
carregado uma única vez e o `tracker` é reiniciado entre vídeos.

#### 5. Parâmetros e configuração por câmera

- **Classes:** `detector.classes` — justifica-se cobrir `person`, `bed`, `chair`, `couch`, `bottle`. Classes clínicas (instrumentos cirúrgicos, etc.) exigem dataset próprio + fine-tuning.
- **Limiar:** `detector.confianca = 0.25` (padrão Ultralytics), `detector.iou = 0.5` (NMS, coerente com `mAP@0.5`).
- **ROIs:** definidas em `areas[]` com `points` (polígono), `largura_ref`/`altura_ref` (resolução de referência), `area_minima`, `classe_filtro`. São **configuráveis por vídeo/câmera**: use `config/leito_uti_07.json` como modelo para outra câmera (muda `fonte.id`, `fonte.video`, `eventos.caminho_jsonl` e calibração dos pontos).
- **Rastreamento:** `detector.rastrear = true`, `tracker = bytetrack.yaml` (ByteTrack) com `monitor.persistencia_quadros = 8`.

#### 6. Baseline e métricas

Sem fine-tuning, o relatório registra as métricas do `yolov8n.pt` **medidas** no
COCO val2017 (`metricas_coco_baseline()`, em `video/metricas.py`):

| Métrica (COCO val2017, `yolov8n.pt`, conf 0,001, IoU NMS 0,5) | Valor |
|---|---|
| mAP@0.5 | **0,528** |
| mAP@0.5:0.95 | **0,366** (model card publica 0,373) |
| Precision | **0,637** |
| Recall | **0,487** |
| AP@0.5 das classes de interesse | person 0,503 · bed 0,425 · couch 0,431 · bottle 0,296 · chair 0,253 |

Para medir de novo:

```bash
# COCO val2017 (5000 imagens) fora do repositório, ex.: ~/datasets/coco_val2017
#   imagens: http://images.cocodataset.org/zips/val2017.zip
#   labels:  https://github.com/ultralytics/assets/releases/download/v0.0.0/coco2017labels.zip (só labels/val2017)
#   data.yaml: path, train/val = images/val2017 e os 80 names de ultralytics/cfg/datasets/coco.yaml
uv run python scripts/avaliar_us07.py ~/datasets/coco_val2017/data.yaml \
  --weights yolov8n.pt --split val --output saida/video/metricas_coco_val2017.json
```

Com um dataset clínico anotado (YOLO, `data.yaml`), o mesmo script mede o domínio do projeto:

```bash
uv run python scripts/avaliar_us07.py caminho/para/data.yaml \
  --weights yolov8n.pt --split val --output saida/video/metricas.json
```

O comando `--baseline` também pode ser usado para obter o mesmo JSON:

```bash
uv run python scripts/avaliar_us07.py --baseline --output saida/video/metricas_baseline.json
```

#### 7. Validação rápida dos artefatos

Verificar eventos contra o schema US04:

```bash
uv run python - <<'PY'
from contratos.validar import validar_arquivo

validos, erros = validar_arquivo("saida/video/eventos_B_D_0002.jsonl")
print("válidos:", len(validos), "| erros:", erros)
PY
```

Conferir relatório por vídeo:

```bash
python - <<'PY'
import json

r = json.load(open("saida/video/relatorio_B_D_0002.json"))
print(r.keys())
print("mAP@0.5:0.95:", r["avaliacao"]["metricas"]["mAP@0.5:0.95"])
PY
```

#### 8. Pela API

O mesmo pipeline está exposto em `POST /video/areas-criticas`; veja
[Detecção em áreas críticas pela API](#detecção-em-áreas-críticas-pela-api-videoareas-criticas).

#### 9. Evidências e conformidade

A rastreabilidade completa (itens 1–8 do DoD) está em
[`docs/relatorio_us07_dod.md`](docs/relatorio_us07_dod.md). A arquitetura, ROIs,
decisões e parâmetros estão em
[`docs/arquitetura/us07_deteccao_areas_criticas.md`](docs/arquitetura/us07_deteccao_areas_criticas.md).

### US08 — movimentos e eventos fora do padrão no vídeo

Combina a postura da US06 (OpenPose) com as áreas críticas da US07 (YOLOv8) e
compara com o **padrão esperado do tipo de vídeo**, definido em
[`config/us08_padroes.yaml`](config/us08_padroes.yaml) (limiares versionados,
nenhum número no código). Cada desvio que persiste pela janela mínima vira um
`EventoAchado` (US04):

| Regra do perfil | `event_type` |
| --- | --- |
| ângulo fora da faixa, ou repetição fora do padrão (alvo absoluto ou série de referência do paciente) | `postura_fora_da_faixa` |
| assimetria esquerda × direita acima do limiar | `assimetria_movimento` |
| queda (quadril desce rápido e não volta) ou giro brusco do tronco mantido | `mudanca_brusca_ou_queda` |
| classe proibida em ROI da US07 por mais que o tolerado | `presenca_indevida_area_critica` |

Perfis: `fisioterapia_geral`, `fisioterapia_abducao_ombro_frontal`,
`fisioterapia_agachamento`, `internacao_leito` e `cirurgia`. Os perfis de
exercício comparam cada repetição com as 3 primeiras da sessão (série de
referência supervisionada), porque ângulos 2D absolutos mudam com a pessoa e a
câmera.

```bash
# 1. Pose da US06 (minutos por vídeo em CPU)
uv run python -m video.pose.run --config video/pose/config.yaml \
  --input data/rehab24/videos/Ex1/PM_001-Camera17-30fps.mp4

# 2. Desvios: eventos, relatório e quadro de evidência anonimizado
uv run python -m video.desvios.run --perfil fisioterapia_abducao_ombro_frontal \
  --pose outputs/pose/PM_001-Camera17-30fps.json --patient-id 1

# Leito: pose + eventos de área crítica da US07 (só --eventos-areas também funciona)
uv run python -m video.desvios.run --perfil internacao_leito \
  --pose outputs/pose/<video>.json --eventos-areas saida/video/eventos_<video>.jsonl

# 3. Avaliação em vídeos rotulados (REHAB24-6 e FallVision)
uv run python scripts/avaliar_us08.py --pose-dir outputs/pose
```

Saídas em `saida/video/desvios/`: `<fonte>_eventos.jsonl`, `<fonte>_relatorio.json`
(padrão aplicado, repetições, desvios com timestamp) e `<fonte>/*.jpg` (quadro do
pico do desvio, com esqueleto e cabeça tarjada). Sem desvios e com análise
confiável, o JSONL recebe um `sem_achados`.

Rastreabilidade ao DoD, protocolo e resultados da avaliação:
[`docs/relatorio_us08_dod.md`](docs/relatorio_us08_dod.md). Testes:
`uv run pytest tests/video/test_desvios.py`.

## Transcrição de consultas com Azure AI Speech (US11)

Usa a API de transcrição rápida (síncrona, o áudio vai na requisição, sem Blob
Storage) em pt-BR, com diarização de até 2 falantes e timestamps por frase e
por palavra. Código em `app/transcription_client.py` (REST, com retry),
`app/transcription_format.py` (JSON do projeto) e `app/transcription_service.py`
(cache por SHA-256 e gravação).

```bash
# Um ou mais áudios -> saida/transcricao/<nome>.json
python scripts/transcrever.py consulta_01.wav
python scripts/transcrever.py consulta_01.wav --medico-falante 1   # falante 1 = médico

# Pronto para a US13: papéis, paciente e início da gravação
python scripts/transcrever.py consulta_01.wav --medico-falante 1 \
  --patient-id 10000032 --recorded-at 2026-10-01T13:00:00Z

# API: POST /transcription (multipart, campo audio_file; opcionais patient_id,
# recorded_at, audio_id e medico_falante) devolve o mesmo JSON
```

Cada JSON traz `audio_id`, `patient_id`, `recorded_at`, `texto_completo` e
`falas`, e cada fala tem `texto`, `falante`, `papel` (`medico`/`paciente` só
com `--medico-falante`), `offset_ms`, `duracao_ms`, `confianca` (por frase; a
API não dá confiança por palavra) e `palavras`.

`audio_id` (padrão: nome do arquivo no script, `job_id` na API), `patient_id` e
`recorded_at` (ISO-8601 UTC, início da gravação) usam os nomes do contrato de
eventos. São o que a US13 precisa para ler o JSON direto. Eles não mudam a
transcrição: rodar de novo só para preenchê-los reaproveita o JSON em cache e
não chama a API.

Falhas: timeouts, erros de conexão e 429/5xx são retentados (3 tentativas, com
`Retry-After` quando houver). O áudio de origem é gravado em
`saida/transcricao/<job>/` antes da chamada e nunca é apagado.

### Medição do WER

Cada áudio de referência precisa de um `.txt` de mesmo nome com a transcrição
de referência, em `dados/transcricao/` (fora do Git). O `--baixar N` baixa uma
amostra do dataset [Eka Medical ASR](https://huggingface.co/datasets/ekacare/eka-medical-asr-evaluation-dataset)
(config `en`, inglês indiano, licença MIT): sorteia N linhas com semente fixa
(`--seed`, padrão 42), converte para WAV 16 kHz mono e grava as referências e o
`manifest.json`. Os `.m4a` originais ficam em `dados/transcricao/origem/`.

```bash
python scripts/avaliar_wer.py --baixar 10                   # amostra de 10, en-IN
python scripts/avaliar_wer.py --baixar 10 --frases-medicas  # com phrase list (se WER > 20%)
python scripts/avaliar_wer.py --locale pt-BR                # áudios próprios em pt-BR
```

Requer `ffmpeg` no PATH (`winget install Gyan.FFmpeg`). O locale padrão do
`avaliar_wer.py` é `en-IN`. O DoD da US11 cita pt-BR, e não há dataset de
referência em português: o relatório registra esse desvio.

Saída: `saida/transcricao/avaliacao/<locale>_<sem|com>_frases/relatorio_wer.md`
(WER global e por áudio, WER sem interjeições como métrica secundária e amostra
de transcrição) e `wer.json`. O DoD pede ao menos 10 áudios; o script avisa
quando há menos. A phrase list em `app/frases_medicas.txt` é em português. Custom
Speech não está implementado.

## Infraestrutura (Terraform)

O projeto usa serviços de IA do **Azure** para os módulos de transcrição de fala e
análise de sentimento, aproveitando a tier gratuita (F0) disponível no Azure:

- **Azure Speech Service** — transcrição de áudio em pt-BR (Speech-to-Text).
- **Azure AI Language** — análise de sentimento de texto em `pt`.
- **Azure Blob Storage** — armazenamento de arquivos de áudio.

A infraestrutura é provisionada via Terraform, na região **`eastus`** (região com melhor
suporte a tier gratuita), e fica em [`terraform/`](terraform/).

### Estrutura de Arquivos Terraform

- `providers.tf` — Configuração do provider Azure
- `variables.tf` — Variáveis do projeto (região, nome, ambiente, etc)
- `resources.tf` — Criação de grupos de recursos, Storage Account e Cognitive Services
- `rbac.tf` — **Atribuições de roles RBAC** (permissões para acessar recursos)
- `outputs.tf` — Saídas (credenciais, endpoints)

### Pré-requisitos adicionais

- [Terraform](https://developer.hashicorp.com/terraform/downloads) `>= 1.9`
- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) configurado
- Uma conta Azure **ativa** com acesso para criar grupos de recursos, contas de storage
  e serviços cognitivos
- Permissão de **Owner** ou **Contributor** na subscription (necessário para criar role assignments)

### Autenticação no Azure

A autenticação é feita via `az login` (seu usuário/conta):

```bash
az login
# Abre navegador para autenticação. Após login, retorna com sucesso
```

O Terraform usa automaticamente as credenciais do `az login` para provisionar recursos.

### Provisionando a infraestrutura

```bash
cd terraform
terraform init
cp terraform.tfvars.example terraform.tfvars
# edite terraform.tfvars conforme necessário
terraform plan -out=tfplan
terraform apply tfplan
terraform output
```

O `apply` cria:

- **Grupo de Recursos** — contêiner para organizar todos os recursos
- **Storage Account** — conta de armazenamento com container privado para áudio
- **Speech Service** (F0) — serviço de transcrição de áudio
- **Language Service** (F0) — serviço de análise de sentimento
- **RBAC Role Assignments** — permissões para o usuário atual acessar os recursos

### Obtendo as credenciais

Após `terraform apply`, extraia as credenciais:

```bash
AZURE_SPEECH_KEY=$(terraform output -raw speech_key)
AZURE_LANGUAGE_KEY=$(terraform output -raw language_key)
AZURE_STORAGE_CONNECTION_STRING=$(terraform output -raw storage_connection_string)
```

Cole no seu `.env`:

```
AZURE_SPEECH_KEY=<value>
AZURE_LANGUAGE_KEY=<value>
AZURE_STORAGE_CONNECTION_STRING=<value>
AZURE_LANGUAGE_ENDPOINT=$(terraform output -raw language_endpoint)
```

### Destruindo a infraestrutura

Como o projeto está em fase de estudo/PoC e Azure oferece tier gratuita F0 sem custos,
você pode manter os recursos provisionados. Para destruir quando necessário:

```bash
cd terraform
terraform destroy
```

### Para CI/CD (Integração Contínua)

Se quiser usar um **Service Principal** em pipelines CI/CD (GitHub Actions, Azure DevOps),
descomente a seção em `terraform/rbac.tf` e adicione as variáveis correspondentes.

### Benefícios da migração para Azure

- **Tier gratuita**: Azure Speech e Language têm quotas mensais gratuitas (F0) sem custo
- **RBAC integrado**: Permissões gerenciadas automaticamente via Terraform
- **Sem credenciais manuais**: Usa autenticação nativa do `az login`
- **Infraestrutura segura**: Storage account privado, HTTPS obrigatório
- **Rede**: Melhor latência em regiões específicas (eastus)

## API (FastAPI)

API local (sem deploy em Azure por ora) que expõe os fluxos de Azure Speech e
Azure Language para teste manual e automatizado, apontando para os recursos Azure
reais provisionados em [`terraform/`](terraform/).

### Rodando a API

```bash
uv run uvicorn app.main:app --reload
```

A API sobe em `http://127.0.0.1:8000`. Documentação interativa (Swagger UI) em
`http://127.0.0.1:8000/docs`.

### Endpoints

| Método | Rota                        | Descrição                                                                                                                                        |
| ------ | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `GET`  | `/health`                   | Verifica se o servidor está no ar (não chama Azure).                                                                                             |
| `POST` | `/transcription`            | Recebe um áudio (`multipart/form-data`, campo `audio_file`) e devolve a transcrição síncrona em `pt-BR`, com falantes e timestamps (US11).     |
| `POST` | `/text/analysis`            | Termos críticos e sentimento na fala do paciente de uma transcrição (US13). Aceita o JSON da US11.                                              |
| `POST` | `/audio/analysis`           | Áudio da consulta → transcrição (US11) → termos críticos e sentimento (US13), numa chamada. Exige `patient_id`, `recorded_at` e `medico_falante`. |
| `POST` | `/sentiment`                | Recebe `{"text": "...", "language_code": "pt"}` e retorna o sentimento via Azure Language Service.                                               |
| `POST` | `/video/pose`               | Recebe um vídeo (`multipart/form-data`, campo `video_file`) e enfileira a análise de pose (US06). Responde `202` com o `job_id`.                 |
| `GET`  | `/video/pose/{job_id}`      | Status (`QUEUED`, `IN_PROGRESS`, `COMPLETED`, `FAILED`) e resultado: métricas de detecção, resumo dos ângulos e artefatos.                       |
| `GET`  | `/video/pose/{job_id}/feedback`  | Resultado em linguagem natural: resumo, pontos positivos, negativos e de alerta.                                                            |
| `GET`  | `/video/pose/{job_id}/keypoints` | Baixa o JSON de pose por quadro (mesmo formato de `outputs/pose/<video_id>.json`).                                                          |
| `GET`  | `/video/pose/{job_id}/angles`    | Baixa o CSV de ângulos por quadro.                                                                                                          |
| `POST` | `/sinais-vitais/news2`           | Recebe um conjunto de sinais vitais e devolve os pontos NEWS2 por parâmetro, o total e o gatilho (US15).                                   |
| `POST` | `/sinais-vitais/medicoes`        | Recebe um lote de medições e devolve os eventos de anomalia gerados. A baseline de cada paciente é mantida entre chamadas (US15).          |

Exemplos com `curl`:

```bash
curl -X POST http://127.0.0.1:8000/sentiment \
  -H "Content-Type: application/json" \
  -d '{"text": "Estou muito satisfeito com o atendimento recebido.", "language_code": "pt"}'

curl -X POST http://127.0.0.1:8000/transcription \
  -F "audio_file=@caminho/para/audio_curto.wav"

curl -X POST "http://127.0.0.1:8000/audio/analysis?detalhes=true" \
  -F "audio_file=@caminho/para/consulta.wav" -F "patient_id=10000032" \
  -F "recorded_at=2026-10-01T13:00:00Z" -F "medico_falante=1"

curl -X POST http://127.0.0.1:8000/video/pose \
  -F "video_file=@caminho/para/agachamento.mp4" \
  -F "patient_id=10000032" -F "source_id=fisio-003" \
  -F "procedure_type=fisioterapia" -F "recorded_at=2026-09-26T15:20:00Z"

curl http://127.0.0.1:8000/video/pose/<job_id>
curl http://127.0.0.1:8000/video/pose/<job_id>/feedback
curl -O http://127.0.0.1:8000/video/pose/<job_id>/angles

curl -X POST http://127.0.0.1:8000/sinais-vitais/news2 \
  -H "Content-Type: application/json" \
  -d '{"fc": 115, "spo2": 93, "fr": 22, "temp": 38.5, "pas": 120}'

curl -X POST http://127.0.0.1:8000/sinais-vitais/medicoes \
  -H "Content-Type: application/json" \
  -d '{"medicoes": [{"patient_id": "10000032", "timestamp": "2026-10-24T08:00:00Z", "sinal": "fc", "valor": 135}]}'
```

### Análise de pose pela API (`/video/pose`)

Mesmo fluxo de `python -m video.pose.run`, sem Azure: precisa só dos pesos do
OpenPose (`scripts/download_openpose_models.sh`) e do `video/pose/config.yaml`.
Sem os pesos, o `POST` responde **503**.

- **Assíncrono.** Em CPU a extração leva minutos (cerca de 5× a duração do
  vídeo com BODY_25 a 10 fps), então o `POST` só grava o upload e devolve o
  `job_id`; consulte o `GET` até `COMPLETED` ou `FAILED`. Os jobs rodam um por
  vez, na ordem de chegada.
- **Campos opcionais do formulário**, validados pelas regras do contrato
  ([`docs/arquitetura/contrato_eventos.md`](docs/arquitetura/contrato_eventos.md)):
  `patient_id` e `encounter_id` (pseudônimo pronto ou ID de origem, que é
  pseudonimizado com `PSEUDONYM_KEY` e nunca gravado), `bed_id`, `source_id`,
  `procedure_type` e `recorded_at` (início da gravação, ISO-8601 em UTC). Valor
  fora do contrato responde **422**.
- **Resultado no formato do contrato.** `patient_id`, `context`, `ingested_at`,
  `window`, `model_version` e `artifacts` saem com os mesmos nomes e tipos do
  `EventoAchado`, prontos para o detector de desvios (US08) montar o evento. A
  API **não emite evento**: a US06 só extrai pose e ângulos, e publicar
  `sem_achados` sem ter rodado a detecção diria à fusão que o vídeo está normal.
- **Arquivos.** Saídas em `outputs/pose/api/<job_id>/` (`<job_id>.json`,
  `<job_id>_angles.csv` e `job.json` com o resultado, relido se a API
  reiniciar). O vídeo enviado é apagado ao fim do job. Jobs em fila ou em
  andamento se perdem se a API reiniciar.
- **Erros.** **400** extensão não aceita (`input.extensions` do config) ou
  arquivo vazio, **413** acima de `VIDEO_MAX_UPLOAD_MB`, **404** job
  desconhecido, **409** download pedido antes de `COMPLETED`. Vídeo ilegível
  vira job `FAILED` com `failure_reason`.

#### Feedback em linguagem natural (`/video/pose/{job_id}/feedback`)

Para quem só quer saber "como foi o vídeo", sem ler ângulos:

```json
{
  "job_id": "pose-01232d72f14c",
  "resumo": "Em 26 s de vídeo a análise encontrou 1 ponto negativo. O tronco ficou inclinado mais de 30° em relação à vertical por 5,0 s (pico de 68° aos 8 s do vídeo). Manter essa inclinação aumenta a carga na coluna lombar; peça para deixar o tronco mais ereto.",
  "pontos_positivos": [
    "A pessoa ficou visível em 88,5% dos quadros, uma boa base para a análise.",
    "Movimento simétrico entre os lados esquerdo e direito em: joelhos, quadris, ombros, cotovelos.",
    "Os joelhos ficaram dentro da faixa de flexão (máxima de 105°, limite de 135°)."
  ],
  "pontos_negativos": ["O tronco ficou inclinado mais de 30° ..."],
  "pontos_de_alerta": [],
  "aviso": "Análise automática a partir de vídeo 2D, ..."
}
```

As frases saem de regras fixas em `video/pose/feedback.py` (sem LLM e sem
serviço externo), então o mesmo vídeo dá sempre o mesmo texto e cada frase cita
o valor medido, o limiar e a duração. O que é avaliado:

| Critério | Negativo | Alerta |
| --- | --- | --- |
| Inclinação do tronco | acima de 30° por ≥ 1 s | acima de 60° por ≥ 1 s |
| Assimetria esquerdo × direito (joelho, quadril, ombro, cotovelo) | acima de 20° por ≥ 1 s | — |
| Flexão do joelho | acima de 135° por ≥ 1 s | — |
| Quadros com a pessoa detectada | entre 50% e 80% | abaixo de 50% |
| Articulação pouco visível | — | ângulo medido em menos de 50% dos quadros |

Critério sem desvio vira ponto positivo. Os limiares ficam em `feedback` no
`video/pose/config.yaml`; são gerais, iguais para qualquer exercício, e **não
foram validados clinicamente**. Duas limitações: os ângulos são projeções 2D
(dependem da posição da câmera) e a inclinação do tronco mede o quanto ele sai
da vertical, não a curvatura da coluna.

Testes (estimador falso, sem pesos nem Azure): `uv run pytest video/tests`.

### Detecção em áreas críticas pela API (`/video/areas-criticas`)

Mesmo fluxo de `scripts/rodar_us07_video.py` (YOLOv8 → ByteTrack → ROI →
evento US04), sem Azure. Precisa só do `yolov8n.pt` (na raiz do repositório;
se faltar, a Ultralytics baixa no primeiro job).

- **Assíncrono**, como a pose: o `POST` grava o upload e devolve o `job_id`; os
  jobs rodam um por vez. O modelo YOLO é carregado no primeiro job e reaproveitado,
  e o rastreamento é reiniciado entre vídeos.
- **ROIs por câmera.** `camera_id` escolhe `config/<camera_id>.json` (ex.:
  `leito_uti_07`); sem ele vale `US07_CONFIG_PATH`. Para outra câmera, crie o
  arquivo com `scripts/gerar_config_us07.py` — não precisa reiniciar a API para
  câmeras novas (o arquivo de uma câmera já usada fica em cache até reiniciar).
- **ROIs na requisição.** O campo `areas` aceita uma lista JSON no mesmo formato
  de `areas[]` dos configs e substitui as ROIs da câmera só neste job:

  ```bash
  curl -X POST http://127.0.0.1:8000/video/areas-criticas \
    -F "video_file=@conteudos/videos/B_D_0016.mp4" -F "camera_id=leito_uti_07" \
    -F 'areas=[{"id":"borda_leito","nome":"Borda do leito","classe_filtro":"person",
               "largura_ref":1920,"altura_ref":1080,
               "points":[[700,300],[1300,300],[1350,1000],[650,1000]]}]'
  ```
- **Demais campos opcionais**: `patient_id`/`encounter_id` (pseudônimo pronto ou
  ID de origem, pseudonimizado com `PSEUDONYM_KEY`), `bed_id`, `source_id`,
  `procedure_type` e `recorded_at` (ISO-8601 em UTC). O que não vier é lido da
  config da câmera. Com `recorded_at`, o `timestamp` do evento é
  `recorded_at + tempo do quadro`; sem ele, parte do início da análise.
- **Resultado.** O `GET /{job_id}` traz `config` (pesos, `confianca` 0,25, `iou`
  0,5, classes, critério e ids das ROIs) e `summary` (quadros, detecções por
  classe, entradas, saídas). O `GET /{job_id}/eventos` traz os `EventoAchado`
  (schema `contratos/schema/evento_achado.v1.schema.json`), com `timestamp`,
  `track_id`, `area_critica_id`, caixa e `tempo_video_s` em `evidence.features`.
  Sem transição, vem um único evento `sem_achados`.
- **Arquivos.** `saida/video/api/<job_id>/eventos.jsonl` e `job.json` (relido se
  a API reiniciar). O vídeo enviado é apagado ao fim do job. Job que estava na fila ou
  em andamento quando a API reiniciou aparece como `FAILED` ("Job interrompido");
  basta reenviar o vídeo.
- **`--reload`.** O `--reload` sem filtro observa também o `.venv` e reinicia a
  API se algo for instalado nele (a Ultralytics instala sozinha o que falta, como
  o `lap` do ByteTrack, se o `uv sync` não foi rodado). Para desenvolver, limite
  as pastas observadas:

  ```bash
  uv run uvicorn app.main:app --reload --reload-dir app --reload-dir video --reload-dir contratos
  ```
- **Erros.** **400** extensão não suportada, arquivo vazio ou `camera_id` com
  caracteres fora de `[A-Za-z0-9_-]`; **404** câmera sem config ou job
  desconhecido; **409** eventos pedidos antes de `COMPLETED`; **413** acima de
  `VIDEO_MAX_UPLOAD_MB`; **422** `areas` inválidas ou `recorded_at` fora de UTC.
  Vídeo ilegível vira job `FAILED` com `failure_reason`.

Testes (detector falso, sem pesos): `uv run pytest tests/video/test_api_areas.py`.

### Tratamento de erros (429 / 401 / 403)

Os clientes Azure SDK incluem mecanismos de retry nativos que lidam com throttling
(429) automaticamente. Erros que sobram são traduzidos em respostas claras
(`app/errors.py`): **429** (throttling persistente), **401** (credenciais Azure
inválidas — revise `AZURE_SPEECH_KEY`, `AZURE_LANGUAGE_KEY`,
`AZURE_STORAGE_CONNECTION_STRING` no `.env`) e **403** (sem permissão — verifique
as permissões RBAC no Azure IAM).

### Smoke tests automatizados

```bash
uv run pytest tests/test_smoke.py -v
```

Os testes chamam a API de verdade contra o Azure real (sem mocks), usando as
credenciais do `.env`. Para o teste de transcrição, coloque um áudio curto pt-BR em
`tests/fixtures/sample_pt_br.wav` (veja
[`tests/fixtures/README.md`](tests/fixtures/README.md)) — se ausente, esse teste é
pulado automaticamente. Se as variáveis Azure não estiverem configuradas, todos os
testes são pulados com uma mensagem explicando o motivo.

## Pipeline de vídeo (US05)

Lê vídeos clínicos (MP4/H.264 e AVI) frame a frame, amostra a taxa configurada,
carimba cada frame com timestamp em ms, anonimiza rostos e grava um JSON de
detecções. Não depende de Azure.

```python
from app.video.config import VideoConfig
from app.video.pipeline import process_video

result = process_video("conteudos/videos/video.mp4", VideoConfig.from_env())
print(result["status"], result["output_json"])
```

Ou pela linha de comando, que também aceita as opções abaixo:

```bash
uv run python -m app.video.pipeline conteudos/videos/video.mp4 --anonymized-video --annotated
```

- `--anonymized-video`: grava `<nome>_anonimizado.mp4`, o vídeo anonimizado **limpo** (sem caixas), usado como insumo de treino.
- `--annotated`: grava `<nome>_anotado.mp4`, com as detecções desenhadas (para inspeção).
- `--method fill|pixelate|mosaic|blur`: como ocultar os rostos (padrão `fill`).
- `--no-anonymize`: desliga a anonimização. Não use com dados pessoais.

**Configuração**

| Variável | Padrão | Descrição |
| --- | --- | --- |
| `VIDEO_TARGET_FPS` | `5` | Fps processado. O passo é `round(fps_origem / target)`; o fps efetivo usado fica em `sampling.effective_fps` no JSON. |
| `VIDEO_ANONYMIZE_METHOD` | `fill` | Como ocultar os rostos: `fill` (tarja preta), `pixelate`, `mosaic` (xadrez preto e branco) ou `blur`. |
| `VIDEO_OUTPUT_DIR` | `saida/video` | Pasta do JSON e dos vídeos gerados (gitignored). |

**Saída (JSON)**

```json
{
  "video": "video.mp4",
  "status": "ok",
  "reason": null,
  "sampling": {"source_fps": 30.0, "target_fps": 5.0, "effective_fps": 5.0, "step": 6},
  "anonymized": true,
  "frames": [
    {"frame_index": 0, "timestamp_ms": 0, "detections": []},
    {"frame_index": 6, "timestamp_ms": 200, "detections": [
      {"label": "person", "confidence": 0.91, "bbox": [0, 0, 0, 0]}
    ]}
  ],
  "output_json": "saida/video/video.json",
  "output_video": null
}
```

`status` pode ser `ok`, `partial` (vídeo parou de entregar frames antes do fim
declarado; os frames lidos são mantidos) ou `failed` (formato inválido ou arquivo
corrompido; o erro é logado e o pipeline não é interrompido).

**Anonimização (LGPD)**: a ocultação dos rostos é aplicada antes da detecção e da
gravação, então nenhum frame com rosto chega ao disco. O detector é o YuNet
(OpenCV), com limiar baixo para priorizar recall; as caixas têm margem de 15% e
uma memória de 3 frames amostrados para cobrir falhas intermitentes. O método
padrão é o preenchimento sólido (`fill`), que não deixa informação visual da
região. A pixelização, o mosaico preto e branco e o blur gaussiano ficam como opções. Rostos muito pequenos,
muito inclinados ou cobertos (máscara, campo cirúrgico) ainda podem escapar; isso
deve ser citado no relatório técnico.

**Detecção**: `app/video/detector.py` define a interface `Detector`. Hoje o
`NullDetector` não detecta nada; YOLOv8 e OpenPose entram plugados nessa interface.

**Observação de dependência**: `opencv-python-headless` está fixado em `<5`, pois
o OpenCV 5 removeu o `CascadeClassifier` e não traz os arquivos Haar.

Testes unitários (sem rede, com vídeo sintético gerado em `tmp_path`):

```bash
uv run pytest tests/video -v
```

### Dataset de origem e licença

Os vídeos clínicos de treino vêm do dataset **REHAB24-6** (fisioterapia), que não é armazenado neste repositório. Os derivados anonimizados gerados pelo pipeline estão sob os termos abaixo.

**Citação obrigatória** (conforme o dataset):

> Černek, A., Sedmidubsky, J., Budikova P.: REHAB24-6: Physical Therapy Dataset for Analyzing Pose Estimation Methods. 17th International Conference on Similarity Search and Applications (SISAP). Springer, 14 pages, 2024.

**Licença**: [Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)](https://creativecommons.org/licenses/by-nc/4.0/). Uso apenas não comercial, com atribuição. Os vídeos derivados foram alterados (anonimização de rostos e amostragem de frames).

Para uso comercial, entre em contato com a VisionCraft pelo e-mail info@visioncraft.ai.

## Anomalias em sinais vitais (US15)

Módulo em `sinais/` que detecta anomalias nas séries de frequência cardíaca,
pressão arterial (sistólica e diastólica), SpO2, frequência respiratória e
temperatura de cada paciente. Roda local, só com numpy e pandas. Não usa o
Azure AI Anomaly Detector, que foi aposentado em 01/10/2026.

Cada medição passa por três etapas, uma de cada vez, como num stream:

1. **Limpeza.** Valores fisiologicamente impossíveis (SpO2 = 0, FC > 300 etc.)
   são descartados e contados. Um valor ausente não é interpolado: o último
   válido continua valendo por um prazo (2 h para FC, PA, SpO2 e FR; 6 h para
   temperatura; 8 h para O2 e consciência). Depois disso o parâmetro fica de
   fora e o evento sai marcado como `news2_parcial`.
2. **Regras clínicas (NEWS2, RCP 2017).** Pontos por parâmetro e total.
   Total 5–6 ou 3 pontos em um parâmetro gera `news2_resposta_urgente`;
   total ≥ 7 gera `news2_emergencia`.
3. **ML não supervisionado.** Compara cada sinal com a baseline do próprio
   paciente. A mudança súbita (`desvio_baseline`) usa um z-score robusto
   (mediana/MAD das últimas 12 h). A tendência (`tendencia_deterioracao`) usa
   a inclinação de uma reta nas últimas 6 h.

Os achados seguem o contrato `EventoAchado` (`contratos/`), com `modality`
`sinais_vitais` e o paciente pseudonimizado.

```
sinais/
├── limpeza.py      # Faixas fisiológicas e validade do último valor
├── news2.py        # Tabela NEWS2, pontos por parâmetro, total e gatilhos
├── detector.py     # DetectorSinais.processar(medicao): estado por paciente e janelas deslizantes
├── mimic.py        # Leitura do chartevents do MIMIC-IV Demo
└── avaliacao.py    # Injeção de anomalias rotuladas e matriz de confusão
```

As regras de limpeza e as decisões de projeto estão em
[`docs/arquitetura/us15_sinais_vitais.md`](docs/arquitetura/us15_sinais_vitais.md).
A rastreabilidade de cada item da DoD está em
[`docs/relatorio_us15_dod.md`](docs/relatorio_us15_dod.md).

### Dados

São 100 pacientes de UTI do **MIMIC-IV Clinical Database Demo 2.2** (PhysioNet,
acesso aberto, cerca de 77 mil medições). Os dados ficam fora do Git, em
`dados/mimic-iv-demo/`:

```bash
uv run python scripts/baixar_sinais.py
```

### Como rodar

`PSEUDONYM_KEY` precisa estar no `.env` (veja [Variáveis de ambiente](#variáveis-de-ambiente)).

```bash
# Confere todos os itens da DoD no terminal (ou só alguns: news2 gatilhos ml ...)
uv run python scripts/demo_us15.py

# Replay em "tempo real": 1 h de dados por segundo; Ctrl+C para parar
uv run python scripts/rodar_us15.py --pacientes 3 --velocidade 3600

# Replay completo sem espera; eventos em saida/sinais/eventos_us15.jsonl
uv run python scripts/rodar_us15.py

# Avaliação (cerca de 1,5 min): saida/sinais/relatorio_us15.json e rotulos_us15.csv
uv run python scripts/avaliar_us15.py

# Testes
uv run pytest tests/sinais
```

Pelo código:

```python
from datetime import UTC, datetime
from sinais import DetectorSinais, Medicao, news2

news2.calcular({"fc": 115, "spo2": 93, "fr": 22, "temp": 38.5}).gatilho   # 'news2_emergencia'

detector = DetectorSinais()
eventos = detector.processar(Medicao("pt_" + "0" * 24, datetime.now(UTC), "fc", 135))
```

A API expõe o mesmo detector em `POST /sinais-vitais/news2` e
`POST /sinais-vitais/medicoes` (veja [Endpoints](#endpoints)). O estado das
baselines fica na memória do processo: é perdido quando a API reinicia e não é
compartilhado entre workers.

### Avaliação

O MIMIC não tem anomalias rotuladas. Por isso a avaliação injeta degraus e
rampas de piora (FC +30 bpm, PAS −30 mmHg, SpO2 −6 % etc.) em metade das janelas
de 6 h de cada paciente e marca essas janelas. Os limiares foram ajustados nos
pacientes de `subject_id` par. As métricas abaixo são as dos ímpares:

|                       | Predito anomalia | Predito normal |
| --------------------- | ---------------: | -------------: |
| **Anomalia injetada** |             1264 |            244 |
| **Normal**            |              574 |            842 |

| Métrica  | Teste | Meta   |
| -------- | ----: | ------ |
| Recall   | 0,838 | ≥ 0,80 |
| Precisão | 0,688 | —      |
| F1       | 0,756 | ≥ 0,70 |

Os falsos positivos incluem variações reais dos pacientes de UTI, que não têm
rótulo. A precisão medida é, portanto, um limite inferior.

### Licença dos dados

O MIMIC-IV Clinical Database Demo é distribuído pelo PhysioNet sob a
[Open Data Commons Open Database License v1.0](https://physionet.org/content/mimic-iv-demo/view-license/2.2/).
Ao usar os dados, cite o dataset conforme a
[página do PhysioNet](https://physionet.org/content/mimic-iv-demo/2.2/).

## Demo ponta a ponta

1. Configure as variáveis de ambiente:

   ```bash
   cp .env.sample .env
   # edite .env e defina GOOGLE_DRIVE_FOLDER_URL com a pasta desejada
   ```

2. Instale as dependências:

   ```bash
   uv sync
   ```

3. Execute o download dos conteúdos:

   ```bash
   python scripts/get_content_script.py
   ```

4. Confira que os arquivos foram baixados:

   ```bash
   find conteudos -type f
   ```

   Exemplo de resultado esperado:

   ```
   conteudos/videos/Samurai_Desembanha_Espada_Rapidamente.mp4
   ```

5. Execute o `main.py` como verificação final:

   ```bash
   python main.py
   ```
