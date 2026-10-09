# monitoramento-medico-multimodal

Projeto de monitoramento médico multimodal. Nesta fase inicial, o foco está na
obtenção e organização dos conteúdos multimodais (vídeos, áudio, documentos,
anomalias e fusão de alertas) que serão utilizados como fonte de dados do
sistema.

## Estrutura do projeto

```
.
├── app/                        # API FastAPI que chama Amazon Transcribe/Comprehend
│   ├── main.py                 # Cria o app, carrega .env, inclui routers, GET /health
│   ├── config.py                # Leitura de AWS_REGION / AWS_S3_BUCKET_NAME
│   ├── aws_clients.py            # Clientes boto3 com retry adaptativo (429)
│   ├── errors.py                 # Tradução de erros AWS em HTTPException (401/403/429)
│   ├── schemas.py                 # Modelos Pydantic de request/response
│   └── routers/
│       ├── transcription.py       # POST /transcription, GET /transcription/{job_name}
│       └── sentiment.py            # POST /sentiment
├── tests/
│   ├── conftest.py               # Fixtures pytest (TestClient, skip sem AWS configurada)
│   ├── test_smoke.py              # Smoke tests reais contra AWS (transcrição + sentiment)
│   └── fixtures/                  # Áudio de exemplo para os testes (gitignored)
├── conteudos/                 # Conteúdos baixados do Google Drive (gitignored)
├── scripts/
│   ├── get_content_script.py  # Download dos conteúdos do Google Drive
│   ├── rodar_us07_video.py   # Executa pipeline de detecção de vídeo
│   └── avaliar_us07.py       # Avalia YOLO em dataset anotado
├── teste/
│   └── conteudos/             # Estrutura de destino dos conteúdos
├── terraform/                 # Infraestrutura AWS (Transcribe, Comprehend, S3, Budget)
│   ├── versions.tf
│   ├── providers.tf
│   ├── variables.tf
│   ├── s3.tf
│   ├── iam.tf
│   ├── budget.tf
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

| Variável | Obrigatória | Descrição | Exemplo |
| --- | --- | --- | --- |
| `GOOGLE_DRIVE_FOLDER_URL` | Sim | URL da pasta do Google Drive cujo conteúdo será baixado para `conteudos/`. O script aborta com erro se não estiver definida. | `https://drive.google.com/drive/folders/1sJN538ANpeud1JzqPMjoNXK_CDXDTo3M?usp=sharing` |
| `AWS_ACCESS_KEY_ID` | Sim (para os módulos de IA) | Access key do IAM user dedicado, criado pelo Terraform e gerado manualmente (veja [Infraestrutura (Terraform)](#infraestrutura-terraform)). | `AKIA...` |
| `AWS_SECRET_ACCESS_KEY` | Sim (para os módulos de IA) | Secret key correspondente ao `AWS_ACCESS_KEY_ID`. Nunca deve ser commitada. | `wJalrXUtnFEMI/...` |
| `AWS_REGION` | Sim (para os módulos de IA) | Região AWS usada por Transcribe/Comprehend/S3. Fixada em `us-east-1`. | `us-east-1` |
| `AWS_S3_BUCKET_NAME` | Sim (para os módulos de IA) | Nome do bucket S3 criado pelo Terraform, usado para áudio de entrada e transcripts. Valor obtido via `terraform output s3_bucket_name`. | `monitoramento-medico-multimodal-media-123456789012` |

O template pronto pode ser copiado de `.env.sample`:

```
GOOGLE_DRIVE_FOLDER_URL=https://drive.google.com/drive/folders/SEU_ID_AQUI?usp=sharing

AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=us-east-1
AWS_S3_BUCKET_NAME=
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
- `saida/video/relatorio_<stem>.json` — resumo único: quadros, detecções, classes, eventos e seção `avaliacao` (baseline COCO `mAP@0.5:0.95 = 0.373`, demais métricas `null` quando não publicadas).

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

Sem dataset anotado, o relatório registra o **baseline COCO** oficial do `yolov8n.pt`:
`mAP@0.5:0.95 = 0.373`; `mAP@0.5`, `precision` e `recall` ficam `null` porque não
são publicados no model card (princípio: nunca fabricar métricas). Ao existir um
dataset YOLO com `data.yaml`, use:

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

#### 8. Evidências e conformidade

A rastreabilidade completa (itens 1–7 do DoD) está em
[`docs/relatorio_us07_dod.md`](docs/relatorio_us07_dod.md). A arquitetura, ROIs,
decisões e parâmetros estão em
[`docs/arquitetura/us07_deteccao_areas_criticas.md`](docs/arquitetura/us07_deteccao_areas_criticas.md).

## Infraestrutura (Terraform)

O projeto usa serviços de IA da **AWS** (não Azure) para os módulos de transcrição de
fala e análise de sentimento:

- **Amazon Transcribe** — transcrição de áudio em pt-BR.
- **Amazon Comprehend** — análise de sentimento de texto em `pt`.

Diferente de serviços cognitivos da Azure, Transcribe e Comprehend não são "recursos"
com endpoint próprio: são chamados via SDK (`boto3`) autenticado por credenciais IAM. A
única peça de infraestrutura de dados necessária é um bucket S3, exigido pelo Transcribe
no modo de transcrição em lote (áudio de entrada e transcript de saída).

Toda a infraestrutura é provisionada via Terraform, na região **`us-east-1`**, e fica em
[`terraform/`](terraform/).

### Pré-requisitos adicionais

- [Terraform](https://developer.hashicorp.com/terraform/downloads) `>= 1.9`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configurado
- Uma credencial AWS **própria** (do operador, diferente da que vai para o `.env`) com
  permissão para criar recursos IAM, S3, Budgets e consultar `sts:GetCallerIdentity`

### Provisionando a infraestrutura

```bash
cd terraform
terraform init
cp terraform.tfvars.example terraform.tfvars
# edite terraform.tfvars e defina budget_alert_email (e demais valores, se quiser mudar os defaults)
terraform plan -out=tfplan
terraform apply tfplan
terraform output
```

O `apply` cria: um bucket S3 (privado, criptografado, com expiração automática de
objetos), um IAM user dedicado (`monitoramento-medico-app` por padrão) com uma policy de
permissões mínimas (apenas as ações necessárias de Transcribe, Comprehend e do bucket
S3) e um AWS Budget mensal com alertas por e-mail em 80% do gasto real e 100% do gasto
previsto.

O Terraform **não** cria a access key do IAM user — isso evitaria expor a secret no
state. Gere a key manualmente após o apply:

```bash
aws iam create-access-key --user-name monitoramento-medico-app
```

Guarde o `AccessKeyId` e o `SecretAccessKey` retornados (não são recuperáveis depois; se
perder, é necessário rotacionar a key) e cole-os no `.env` do projeto junto com
`AWS_REGION=us-east-1` e o `s3_bucket_name` obtido em `terraform output`.

### Destruindo a infraestrutura

Como o projeto está em fase de estudo/PoC, destrua os recursos quando não estiverem em
uso para evitar custos:

```bash
cd terraform
terraform destroy
```

### O que esta infraestrutura NÃO cobre

Ficam para uma fase de código posterior, fora do escopo do Terraform:

- Scripts Python de smoke test (transcrição de áudio curto pt-BR e análise de
  sentimento de frase em `pt`, ambos esperando HTTP 200).
- Retry exponencial para HTTP 429 (throttling) e mensagens claras para 401/403.
- Detecção de anomalias — decisão tomada independente de cloud: será implementada como
  código próprio do projeto, não como um serviço gerenciado (a Azure aposentou o AI
  Anomaly Detector em 01/10/2026, então o projeto nunca dependeu dele).

## API (FastAPI)

API local (sem deploy na AWS por ora) que expõe os fluxos de Amazon Transcribe e
Amazon Comprehend para teste manual e automatizado, apontando para os recursos AWS
reais provisionados em [`terraform/`](terraform/).

### Rodando a API

```bash
uv run uvicorn app.main:app --reload
```

A API sobe em `http://127.0.0.1:8000`. Documentação interativa (Swagger UI) em
`http://127.0.0.1:8000/docs`.

### Endpoints

| Método | Rota | Descrição |
| --- | --- | --- |
| `GET` | `/health` | Verifica se o servidor está no ar (não chama AWS). |
| `POST` | `/transcription` | Recebe um áudio (`multipart/form-data`, campo `audio_file`), sobe para o S3 e inicia um job assíncrono no Amazon Transcribe (`pt-BR`). |
| `GET` | `/transcription/{job_name}` | Consulta o status/resultado de um job de transcrição. |
| `POST` | `/sentiment` | Recebe `{"text": "...", "language_code": "pt"}` e retorna o sentimento via Amazon Comprehend. |

Exemplos com `curl`:

```bash
curl -X POST http://127.0.0.1:8000/sentiment \
  -H "Content-Type: application/json" \
  -d '{"text": "Estou muito satisfeito com o atendimento recebido.", "language_code": "pt"}'

curl -X POST http://127.0.0.1:8000/transcription \
  -F "audio_file=@caminho/para/audio_curto.wav"

curl http://127.0.0.1:8000/transcription/<job_name>
```

### Tratamento de erros (429 / 401 / 403)

Os clientes boto3 usam retry adaptativo nativo do `botocore`
(`Config(retries={"max_attempts": 5, "mode": "adaptive"})`), que já faz backoff
exponencial com jitter para throttling (`ThrottlingException`/429) antes de qualquer
erro chegar na API. Erros que sobram são traduzidos em respostas claras
(`app/errors.py`): **429** (throttling persistente), **401** (credenciais AWS
inválidas — revise `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` no `.env`) e **403**
(sem permissão — revise a policy em `terraform/iam.tf`).

### Smoke tests automatizados

```bash
uv run pytest tests/test_smoke.py -v
```

Os testes chamam a API de verdade contra a AWS real (sem mocks), usando as
credenciais do `.env`. Para o teste de transcrição, coloque um áudio curto pt-BR em
`tests/fixtures/sample_pt_br.wav` (veja
[`tests/fixtures/README.md`](tests/fixtures/README.md)) — se ausente, esse teste é
pulado automaticamente. Se as variáveis `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
`AWS_REGION` ou `AWS_S3_BUCKET_NAME` não estiverem configuradas, todos os testes são
pulados com uma mensagem explicando o motivo.

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