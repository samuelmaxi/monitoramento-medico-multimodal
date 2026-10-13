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
│   └── routers/
│       ├── transcription.py       # POST /transcription, GET /transcription/{job_name}
│       └── sentiment.py            # POST /sentiment
├── tests/
│   ├── conftest.py               # Fixtures pytest (TestClient, skip sem Azure configurada)
│   ├── test_smoke.py              # Smoke tests reais contra Azure (transcrição + sentiment)
│   └── fixtures/                  # Áudio de exemplo para os testes (gitignored)
├── conteudos/                 # Conteúdos baixados do Google Drive (gitignored)
├── scripts/
│   ├── get_content_script.py  # Download dos conteúdos do Google Drive
│   ├── rodar_us07_video.py   # Executa pipeline de detecção de vídeo
│   └── avaliar_us07.py       # Avalia YOLO em dataset anotado
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

| Variável | Obrigatória | Descrição | Exemplo |
| --- | --- | --- | --- |
| `GOOGLE_DRIVE_FOLDER_URL` | Sim | URL da pasta do Google Drive cujo conteúdo será baixado para `conteudos/`. O script aborta com erro se não estiver definida. | `https://drive.google.com/drive/folders/1sJN538ANpeud1JzqPMjoNXK_CDXDTo3M?usp=sharing` |
| `AZURE_SPEECH_KEY` | Sim (para transcrição) | Chave da API do serviço Azure Speech. Obtida via `terraform output speech_key`. | `a1b2c3d4e5f6...` |
| `AZURE_SPEECH_REGION` | Sim (para transcrição) | Região do serviço Azure Speech. Fixada em `eastus` para usar tier gratuita. | `eastus` |
| `AZURE_LANGUAGE_ENDPOINT` | Sim (para análise de sentimento) | Endpoint do serviço Azure AI Language. Obtido via `terraform output language_endpoint`. | `https://monitoramento-language-dev.cognitiveservices.azure.com/` |
| `AZURE_LANGUAGE_KEY` | Sim (para análise de sentimento) | Chave da API do serviço Azure Language. Obtida via `terraform output language_key`. | `a1b2c3d4e5f6...` |
| `AZURE_STORAGE_CONNECTION_STRING` | Sim (para transcrição) | Connection string do Azure Blob Storage. Obtida via `terraform output storage_connection_string`. | `DefaultEndpointsProtocol=https;AccountName=...` |
| `AZURE_BLOB_CONTAINER_NAME` | Não | Nome do container Blob Storage para arquivos de áudio. Padrão: `audio-transcripts`. | `audio-transcripts` |

O template pronto pode ser copiado de `.env.sample`:

```
GOOGLE_DRIVE_FOLDER_URL=https://drive.google.com/drive/folders/SEU_ID_AQUI?usp=sharing

AZURE_SPEECH_KEY=
AZURE_SPEECH_REGION=eastus
AZURE_LANGUAGE_ENDPOINT=
AZURE_LANGUAGE_KEY=
AZURE_STORAGE_CONNECTION_STRING=
AZURE_BLOB_CONTAINER_NAME=audio-transcripts
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

| Método | Rota | Descrição |
| --- | --- | --- |
| `GET` | `/health` | Verifica se o servidor está no ar (não chama Azure). |
| `POST` | `/transcription` | Recebe um áudio (`multipart/form-data`, campo `audio_file`), sobe para Azure Blob Storage e inicia um job assíncrono para transcrição (`pt-BR`). |
| `GET` | `/transcription/{job_name}` | Consulta o status/resultado de um job de transcrição. |
| `POST` | `/sentiment` | Recebe `{"text": "...", "language_code": "pt"}` e retorna o sentimento via Azure Language Service. |

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