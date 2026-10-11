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
│   └── download_openpose_models.sh  # Pesos do OpenPose com checksum (US06)
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
| `AZURE_LANGUAGE_ENDPOINT`         | Sim (para análise de sentimento) | Endpoint do serviço Azure AI Language. Obtido via `terraform output language_endpoint`.                                      | `https://monitoramento-language-dev.cognitiveservices.azure.com/`                      |
| `AZURE_LANGUAGE_KEY`              | Sim (para análise de sentimento) | Chave da API do serviço Azure Language. Obtida via `terraform output language_key`.                                          | `a1b2c3d4e5f6...`                                                                      |
| `AZURE_STORAGE_CONNECTION_STRING` | Sim (para transcrição)           | Connection string do Azure Blob Storage. Obtida via `terraform output storage_connection_string`.                            | `DefaultEndpointsProtocol=https;AccountName=...`                                       |
| `AZURE_BLOB_CONTAINER_NAME`       | Não                              | Nome do container Blob Storage para arquivos de áudio. Padrão: `audio-transcripts`.                                          | `audio-transcripts`                                                                    |

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

## Análise postural com OpenPose (US06)

Módulo em `video/pose/` que extrai a pose de vídeos clínicos (fisioterapia e
movimentação no leito) com os pesos oficiais do **OpenPose** (CMU) via
`cv2.dnn.readNetFromCaffe` — sem compilar o OpenPose — e calcula ângulos
articulares por quadro. A detecção de desvios fica para a US08; aqui só são
gerados os dados que ela consome.

```
video/
├── requirements.txt          # Dependências fixadas do módulo (Python 3.14)
├── pose/
│   ├── config.yaml           # Modelo, fps, limiares, caminhos (nada fixo no código)
│   ├── extract.py            # API: extract_pose(video, config) e compute_angles(seq)
│   ├── run.py                # CLI de lote: JSON + CSV de ângulos + summary.csv
│   ├── visualize.py          # Figuras com o esqueleto desenhado
│   ├── openpose.py           # Inferência cv2.dnn + agrupamento por PAF
│   ├── angles.py             # Geometria dos ângulos
│   ├── sequence.py           # PoseSequence e serialização JSON
│   ├── models.py             # Keypoints e pares do BODY_25 e do COCO
│   └── config.py             # Leitura e validação do config.yaml
└── tests/                    # pytest (ângulos, keypoints null, amostragem, config)
```

### Instalação

Requer Python 3.14.

```bash
python3.14 -m venv .venv-pose
source .venv-pose/bin/activate
pip install -r video/requirements.txt
```

O `opencv-python-headless` do PyPI roda em CPU. O backend CUDA
(`model.backend: auto` ou `cuda`) só é usado se o OpenCV instalado tiver sido
compilado com CUDA e houver GPU; caso contrário o módulo avisa e usa CPU.

### Download dos modelos

```bash
scripts/download_openpose_models.sh            # BODY_25 (padrão) em models/openpose/
scripts/download_openpose_models.sh coco       # COCO
scripts/download_openpose_models.sh all
```

O script baixa o `.prototxt` do repositório oficial da CMU no GitHub (commit
fixado) e o `.caffemodel` do espelho do repositório OpenPose no Hugging Face
(revisão fixada), porque o servidor original da CMU está fora do ar. Cada
arquivo tem o SHA-256 conferido; se não bater, o arquivo é apagado e o script
falha. Os pesos ficam em `models/` e não são versionados.

### Datasets

Os caminhos ficam em `video/pose/config.yaml` (`datasets.*`).

- **REHAB24-6** ([Zenodo](https://zenodo.org/records/13305826), CC BY-NC 4.0):
  `Segmentation.csv`, `joints_names.txt` e `videos.zip` descompactado em
  `data/rehab24/videos/` (`Ex1..Ex6/PM_XXX-Camera17-30fps.mp4` e
  `PM_XXX-Camera18-30fps-transposed.mp4`).
- **FallVision** ([Harvard Dataverse](https://doi.org/10.7910/DVN/75QPKK), CC0):
  só os RAR de **Raw Video** (os de _Mask Video_ são ignorados pelo filtro
  `input.exclude_substrings`), extraídos em `data/fallvision/` mantendo a
  estrutura `Fall|No Fall/Bed|Chair|Stand/Raw Video/<pacote>/*.mp4`. O dataset
  completo tem ~50 GB; baixe só os pacotes necessários, por exemplo:

  ```bash
  mkdir -p "data/fallvision/Fall/Bed/Raw Video"
  curl -L -o /tmp/f_raw_b_3.rar https://dataverse.harvard.edu/api/access/datafile/8138614
  bsdtar -xf /tmp/f_raw_b_3.rar -C "data/fallvision/Fall/Bed/Raw Video"   # ou unrar x
  ```

### Como rodar

Sempre a partir da raiz do repositório:

```bash
# Lote: pasta(s) ou arquivo(s), varridas recursivamente
python -m video.pose.run --input data/rehab24/videos/Ex1 --config video/pose/config.yaml

# FallVision: use --id-root para o video_id incluir as pastas (os nomes de
# arquivo se repetem entre pacotes, ex.: B_N_01.mp4)
python -m video.pose.run --input data/fallvision --id-root data/fallvision \
    --config video/pose/config.yaml

# Opções: --limit N, --skip-existing (retoma um lote), -v

# Figuras (depois do run)
python -m video.pose.visualize --config video/pose/config.yaml rehab24     # 1 rep. correta + 1 incorreta
python -m video.pose.visualize --config video/pose/config.yaml fallvision  # 1 queda da cama
python -m video.pose.visualize --config video/pose/config.yaml frames \
    --video-id PM_000-Camera17-30fps --start 180 --end 377

# Testes
python -m pytest video/tests
```

#### Com um vídeo próprio

`--input` aceita qualquer vídeo que o OpenCV abra (`.mp4`, `.mov`, `.avi`,
`.mkv`), não só os dos datasets. O `video_id` é o nome do arquivo sem extensão.
As figuras usam o subcomando `frames`, com o trecho em quadros (segundos × fps):

```bash
python -m video.pose.run --config video/pose/config.yaml --input ~/Downloads/agachamento.mp4
python -m video.pose.visualize --config video/pose/config.yaml frames \
    --video-id agachamento --start 60 --end 180
```

Dicas de gravação:

- Corpo inteiro no quadro durante todo o movimento, câmera parada e boa luz.
- Os ângulos são 2D, medidos na imagem: grave de perfil para medir flexão de
  joelho, quadril e inclinação do tronco. De frente, a flexão quase não aparece.
- De preferência, só uma pessoa no quadro. Com mais de uma, fica a de
  esqueleto mais completo.
- Caminhos com "mask" são ignorados (`input.exclude_substrings`).
- Se o vídeo não abrir (`status=erro` no `summary.csv`), converta para H.264:
  `ffmpeg -i video.mov -c:v libx264 video.mp4`.

Uso como biblioteca:

```python
from video.pose.config import load_config
from video.pose.extract import extract_pose, compute_angles

cfg = load_config("video/pose/config.yaml")
seq = extract_pose("data/rehab24/videos/Ex1/PM_000-Camera17-30fps.mp4", cfg)
df = compute_angles(seq)            # pandas.DataFrame, um quadro por linha
seq.save_json("outputs/pose/PM_000-Camera17-30fps.json")
```

Um vídeo inválido levanta `VideoReadError`; no CLI o erro vai para o log
(`outputs/pose/run.log`) e para o `summary.csv` com `status=erro`, e o lote
continua.

### Formato das saídas (`outputs/pose/`)

**`<video_id>.json`** — pose por quadro processado:

```json
{
  "schema_version": "1.0",
  "video_id": "PM_001-Camera17-30fps",
  "source_path": "data/rehab24/videos/Ex1/PM_001-Camera17-30fps.mp4",
  "metadata": {
    "model": "BODY_25",
    "backend": "cpu",
    "input_height": 368,
    "source_fps": 30.0,
    "target_fps": 10.0,
    "processed_fps": 10.0,
    "conf_threshold": 0.3,
    "min_valid_keypoints": 8,
    "frame_width": 1920,
    "frame_height": 1080,
    "n_source_frames": 3016,
    "n_processed_frames": 1006,
    "module_version": "0.1.0",
    "opencv_version": "4.14.0",
    "created_at": "..."
  },
  "keypoint_names": ["Nose", "Neck", "RShoulder", "..."],
  "frames": [
    {
      "frame_idx": 0,
      "timestamp_ms": 0,
      "n_valid_keypoints": 24,
      "skeleton_detected": true,
      "keypoints": {
        "Nose": [756.1, 259.2, 0.915],
        "LBigToe": null,
        "...": "..."
      }
    }
  ]
}
```

- `frame_idx` é o índice do quadro no vídeo original (o mesmo do
  `Segmentation.csv` do REHAB24-6); `timestamp_ms = frame_idx * 1000 / fps`.
- Keypoint = `[x, y, conf]` em pixels do quadro original, ou `null` se não
  detectado ou com `conf < keypoints.conf_threshold`.
- Só a pessoa principal é registrada (a com mais keypoints ligados pelos PAFs).
- Esquerdo/direito são do paciente (`LKnee` = joelho esquerdo).

**`<video_id>_angles.csv`** — uma linha por quadro processado, ângulos em graus:

| Coluna                                                                               | Definição                                                                   |
| ------------------------------------------------------------------------------------ | --------------------------------------------------------------------------- |
| `frame_idx`, `timestamp_ms`                                                          | Iguais ao JSON                                                              |
| `joelho_esquerdo`, `joelho_direito`                                                  | Quadril–joelho–tornozelo                                                    |
| `quadril_esquerdo`, `quadril_direito`                                                | Ombro–quadril–joelho                                                        |
| `ombro_esquerdo`, `ombro_direito`                                                    | Quadril–ombro–cotovelo                                                      |
| `cotovelo_esquerdo`, `cotovelo_direito`                                              | Ombro–cotovelo–punho                                                        |
| `inclinacao_tronco`                                                                  | Ângulo entre centro do quadril→pescoço e a vertical (0° em pé, 90° deitado) |
| `assimetria_joelho`, `assimetria_quadril`, `assimetria_ombro`, `assimetria_cotovelo` | \|esquerdo − direito\|                                                      |

Os ângulos articulares são internos (0°–180°): membro esticado ≈ 180°, e a
flexão é `180 − ângulo`. São ângulos 2D na imagem, logo dependem do ponto de
vista da câmera. Ângulo vazio (NaN) = faltou algum keypoint necessário.

**`summary.csv`** — uma linha por vídeo (lotes seguintes atualizam pelo
`video_id`): `status`, `erro`, `frames_processados`, `frames_com_esqueleto`,
`pct_frames_com_esqueleto` (quadros com ≥ `min_valid_keypoints` keypoints
válidos), fps de origem e processado, duração e tempo de processamento.

**`figures/`** — mosaicos com o esqueleto desenhado (azul = lado esquerdo,
vermelho = direito, verde = linha média).

### Licenças

- **OpenPose** (código e modelos): licença acadêmica da Carnegie Mellon
  University, **uso não comercial apenas** — pesquisa, ensino e avaliação. Uso
  comercial exige licença da CMU. Veja o
  [LICENSE](https://github.com/CMU-Perceptual-Computing-Lab/openpose/blob/master/LICENSE).
  Cite: Cao et al., _OpenPose: Realtime Multi-Person 2D Pose Estimation using
  Part Affinity Fields_, IEEE TPAMI, 2019.
- **REHAB24-6**: uso acadêmico/não comercial (CC BY-NC 4.0); cite Černek,
  Sedmidubsky e Budikova, SISAP 2024.
- **FallVision**: CC0 1.0.

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
| `POST` | `/transcription`            | Recebe um áudio (`multipart/form-data`, campo `audio_file`), sobe para Azure Blob Storage e inicia um job assíncrono para transcrição (`pt-BR`). |
| `GET`  | `/transcription/{job_name}` | Consulta o status/resultado de um job de transcrição.                                                                                            |
| `POST` | `/sentiment`                | Recebe `{"text": "...", "language_code": "pt"}` e retorna o sentimento via Azure Language Service.                                               |

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
