# monitoramento-medico-multimodal

Projeto de monitoramento médico multimodal. Nesta fase inicial, o foco está na
obtenção e organização dos conteúdos multimodais (vídeos, áudio, documentos,
anomalias e fusão de alertas) que serão utilizados como fonte de dados do
sistema.

## Estrutura do projeto

```
.
├── conteudos/                 # Conteúdos baixados do Google Drive (gitignored)
├── scripts/
│   └── get_content_script.py  # Download dos conteúdos do Google Drive
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