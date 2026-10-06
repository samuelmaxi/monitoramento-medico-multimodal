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

Execute a configuração de exemplo, com limite opcional de quadros para smoke test:

```bash
uv run python scripts/rodar_us07_video.py config/exemplo_us07.json --max-frames 30
```

A configuração define o vídeo, pesos YOLO, classes de interesse, polígono(s) ROI,
critérios de entrada/saída e destino JSONL compatível com o contrato US04. Para
processar o vídeo configurado por inteiro, omita `--max-frames`. Os pesos locais
são obtidos pelo Ultralytics e podem ser substituídos por um checkpoint próprio.

A execução registrada com `yolov8n.pt` usa pesos COCO: identifica `person`, mas
não conhece rótulos clínicos como instrumento cirúrgico. Esses rótulos exigem
um dataset especializado, anotado no formato YOLO. Para métricas quantitativas
reprodutíveis (mAP@0.5, mAP@0.5:0.95, precisão e recall), forneça o `data.yaml`
e o split anotado:

```bash
uv run python scripts/avaliar_us07.py caminho/para/data.yaml \\
  --weights yolov8n.pt --split val --output saida/video/metricas.json
```

Um vídeo de demonstração sem ground truth pode validar o fluxo e ser inspecionado
qualitativamente, mas não produz mAP, precisão ou recall válidos.

A especificação do fluxo, das ROIs e da avaliação da US07 está em
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