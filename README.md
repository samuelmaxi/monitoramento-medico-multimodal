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
│   └── routers/
│       ├── transcription.py       # POST /transcription (síncrono, US11)
│       ├── text_analysis.py       # POST /text/analysis (US13)
│       ├── audio_analysis.py      # POST /audio/analysis (áudio → US11 → US13)
│       ├── sentiment.py            # POST /sentiment
│       └── video_pose.py           # POST /video/pose, GET /video/pose/{job_id}[/keypoints|/angles]
├── tests/
│   ├── conftest.py               # Fixtures pytest (TestClient, skip sem Azure configurada)
│   ├── test_smoke.py              # Smoke tests reais contra Azure (transcrição + sentiment)
│   └── fixtures/                  # Áudio de exemplo para os testes (gitignored)
├── conteudos/                 # Conteúdos baixados do Google Drive (gitignored)
├── scripts/
│   ├── get_content_script.py  # Download dos conteúdos do Google Drive
│   ├── eval_us13.py           # Recall/precision dos termos críticos (US13)
│   └── download_openpose_models.sh  # Pesos do OpenPose com checksum (US06)
├── video/
│   └── pose/                  # Análise postural com OpenPose (US06)
├── audio/
│   ├── text/                  # Termos críticos e sentimento nas transcrições (US13)
│   └── tests/                 # Testes da US13 com a API mockada
├── data/
│   └── text_samples/          # Consultas simuladas e anotações da US13 (versionadas)
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
| `PSEUDONYM_KEY`                   | Só para enviar IDs de origem     | Chave HMAC (≥ 32 bytes) de `contratos/pseudonimizacao.py`. Usada por `POST /video/pose` e pela US13 quando `patient_id`/`encounter_id` não chegam já pseudonimizados. | `python -c "import secrets; print(secrets.token_hex(32))"`                             |
| `POSE_CONFIG_PATH`                | Não                              | `config.yaml` usado pela API de pose. Padrão: `video/pose/config.yaml`.                                                      | `video/pose/config.yaml`                                                               |
| `VIDEO_MAX_UPLOAD_MB`             | Não                              | Tamanho máximo do vídeo aceito por `POST /video/pose`. Padrão: `500`.                                                        | `500`                                                                                  |

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

O `uv sync` da raiz também instala essas dependências (nas mesmas versões), e é
esse ambiente que a API usa em `POST /video/pose`. O OpenCV fica fixado em
4.14: o 5.x removeu o `cv2.dnn.readNetFromCaffe`.

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

## Termos críticos e sentimento nas transcrições (US13)

Lê as transcrições diarizadas da US11, envia **só as falas do paciente** ao Azure AI Language e emite eventos no contrato da US04 (`modality="texto"`):

- **Text Analytics for health** extrai sintomas, medicamentos, dosagens e diagnósticos, com categoria, confiança, nome normalizado e asserções.
- Um **filtro de asserção** descarta negações, sintomas de terceiros e hipóteses, e rebaixa o histórico passado.
- As entidades que restam são comparadas com a lista versionada de termos críticos.
- O **sentimento** é calculado por sentença e resumido por consulta.

A fala do médico ("você sente falta de ar?") não é enviada à Azure e nunca gera achado. Ela só entra como contexto, em `pergunta_medico` na evidência.

**Do áudio aos eventos**: a entrada é o JSON da US11, transcrito com `--medico-falante` (é ele que separa a fala do paciente), `--patient-id` e `--recorded-at`. Pela API, `POST /audio/analysis` faz as duas etapas numa chamada.

```bash
pip install -r audio/requirements.txt         # ou uv sync
python scripts/transcrever.py consulta_01.wav --medico-falante 1 --patient-id 10000032 --recorded-at 2026-10-01T13:00:00Z
python -m audio.text.run --input saida/transcricao/consulta_01.json --config audio/text/config.yaml
python -m audio.text.run --input data/text_samples --config audio/text/config.yaml   # amostras simuladas
python scripts/eval_us13.py                   # recall/precision/F1 → outputs/text_analysis/eval.md
python -m contratos.validar outputs/text_analysis/consulta-*.json
curl -X POST localhost:8000/text/analysis -H 'Content-Type: application/json' --data @data/text_samples/consulta-07.json   # pela API
pytest audio/tests                            # API mockada, sem rede
```

| Arquivo | Conteúdo |
| --- | --- |
| `audio/text/config.yaml` | Idiomas, limites e lotes do serviço, retry, cache, rótulos de falante, marcadores de passado e limiar de sentimento |
| `audio/text/critical_terms.yaml` | Lista versionada de 15 termos críticos: nome canônico, sinônimos em pt-BR, nomes normalizados (UMLS), categorias e severidade |
| `audio/text/alerts.py` | **Stub** do motor de alertas: assina o barramento e só registra no log os eventos de severidade alta. Será trocado pela US19 |
| `data/text_samples/` | 15 consultas simuladas, com negações, sintomas de terceiros, hipóteses e histórico passado de propósito, e o `annotations.csv` |

**Variáveis de ambiente**

| Variável | Uso |
| --- | --- |
| `AZURE_LANGUAGE_ENDPOINT` | Endpoint do recurso Azure AI Language (`terraform output language_endpoint`) |
| `AZURE_LANGUAGE_KEY` | Chave do recurso. Só por variável de ambiente; nunca no código ou no config |
| `PSEUDONYM_KEY` | Pseudonimiza o `patient_id` da transcrição (contrato da US04) |
| `EVENTOS_JSONL` | Barramento local onde os eventos são publicados. Padrão: `saida/eventos.jsonl` |
| `TEXT_CONFIG_PATH` | Config usado pela rota `POST /text/analysis`. Padrão: `audio/text/config.yaml` |

**Códigos de idioma**

| Serviço | Código | Por quê |
| --- | --- | --- |
| Text Analytics for health | `pt` | É o único código de português que o serviço aceita; não existe variante `pt-BR` |
| Análise de sentimento | `pt-BR` | No sentimento, `pt` é interpretado como **pt-PT**. Para o português do Brasil é preciso pedir `pt-BR` explicitamente |

**Limites do serviço**: até 5.120 caracteres por documento. Falas mais longas são divididas por sentença; uma sentença maior que isso é cortada no último espaço. O sentimento aceita até 10 documentos por requisição e o health até 25. Os offsets devolvidos pelo serviço são remapeados para a posição na fala original.

**Erros**: HTTP 429 (e 5xx transitório) tem retry exponencial (1 s, 2 s, 4 s…, até `azure.retry.max_delay_s`), e o `Retry-After` do serviço tem precedência. HTTP 401 e 403 interrompem o lote com uma mensagem que aponta a causa provável. O retry interno do SDK fica desligado, para haver uma política só, visível no log.

**Cache**: cada resposta fica em `saida/cache_azure/language/`, com o hash de operação, idioma, versão do modelo e texto como chave. Rodar de novo, regravar o vídeo ou ajustar limiares não repete chamadas à Azure. Apague a pasta para forçar uma nova análise.

**Filtro de asserção** (cada descarte é registrado no log e em `detalhes/<audio_id>.json` com o motivo):

| Asserção | Exemplo | Decisão |
| --- | --- | --- |
| certeza `negative` ou `negative_possible` | "nego dor no peito" | descarta |
| associação `other` | "meu pai teve infarto" | descarta |
| condicionalidade `hypothetical` ou `conditional` | "se a falta de ar piorar" | descarta |
| temporalidade `past` | "há cinco anos tive uma convulsão" | mantém, com a severidade um nível abaixo (alta → media → baixa → info) |

> **Temporalidade:** a API GA (`2023-04-01`, usada pelo `azure-ai-textanalytics` 5.3.0) devolve certeza, condicionalidade e associação, mas **não** temporalidade. O cliente lê `temporality` da resposta crua, caso o serviço passe a devolvê-la. Enquanto isso não acontece, a sentença da entidade é comparada com os marcadores de passado de `assertions.past_markers` (ex.: "há N anos", "já tive", "ano passado"). A origem de cada decisão fica em `temporalidade_fonte` (`servico` ou `regra_local`).

**Casamento com a lista**: a categoria da entidade precisa estar entre as do termo. Além disso, o nome normalizado precisa coincidir com um dos `normalized`, ou o texto da entidade precisa conter um dos sinônimos como palavra inteira. Caixa e acentos são ignorados. Um termo repetido na consulta gera um único evento, com `ocorrencias`.

**Sentimento**: uma sentença é negativa quando o score negativo é ≥ `sentiment.negative_threshold` (0,7). A consulta é predominantemente negativa quando a fração de sentenças negativas do paciente é ≥ `sentiment.min_negative_ratio` (0,5). Nesse caso sai um evento `sentimento_negativo` (severidade `baixa`) com o score médio como `score`.

**Saída**

| Caminho | Conteúdo |
| --- | --- |
| `outputs/text_analysis/<audio_id>.json` | Lista de `EventoAchado`: `termo_critico`, `sentimento_negativo` ou `sem_achados` |
| `outputs/text_analysis/detalhes/<audio_id>.json` | Todas as entidades com asserções e decisão, os descartes com motivo e o sentimento por sentença com os três scores e o resumo da consulta |
| `outputs/text_analysis/run.log` | Log do lote, incluindo o motivo de cada descarte |
| `saida/eventos.jsonl` | Barramento local; o stub de alertas recebe daqui os eventos de severidade alta |

Formato de um evento `termo_critico` (resumido e com valores ilustrativos; o exemplo real da rodada fica no `eval.md`):

```json
{
  "modality": "texto",
  "event_type": "termo_critico",
  "score": 0.97,
  "severity": "alta",
  "evidence": {
    "summary": "Paciente relata dor torácica: “dor no peito” (afirmado, sem negação)",
    "features": {
      "termo": "dor torácica", "texto": "dor no peito", "categoria": "SymptomOrSign",
      "nome_normalizado": "Chest Pain", "confianca": 0.97,
      "certeza": "positive", "condicionalidade": null, "associacao": "subject",
      "temporalidade": null, "rebaixada": false, "falante": "paciente", "offset_ms": 6512,
      "trecho": "Desde ontem à noite estou com uma dor no peito muito forte, ...",
      "pergunta_medico": "Bom dia. O que trouxe o senhor aqui hoje?"
    },
    "models": {"azure-text-analytics-for-health": "<modelVersion>", "idioma-health": "pt", "idioma-sentimento": "pt-BR"},
    "artifacts": [{"kind": "transcricao", "uri": "data/text_samples/consulta-01.json", "start_ms": 3615, "end_ms": 11675}]
  },
  "model_version": "texto-nlp@0.1.0"
}
```

**Formatos de transcrição aceitos**. O principal é o JSON da US11 (seção acima): cada fala vira um segmento e o falante é o `papel`. Sem `medico_falante` não há papel, e a leitura falha em vez de mandar a fala do médico para a análise. Uma fala sem falante na diarização é registrada no log e ignorada. `audio_id`, `patient_id` e `recorded_at` são obrigatórios nos dois formatos; `recorded_at` + `offset_ms` dá o `timestamp` UTC que o contrato exige. O segundo é o formato compacto das amostras simuladas (`data/text_samples/`):

```json
{
  "audio_id": "consulta-01",
  "patient_id": "sim-paciente-01",
  "recorded_at": "2026-10-01T13:00:00Z",
  "segments": [{"speaker": "paciente", "text": "...", "offset_ms": 3615, "duration_ms": 8060, "confidence": 0.89}]
}
```

**API**: `POST /text/analysis` recebe a transcrição em qualquer dos dois formatos e devolve os eventos (com `?detalhes=true`, também entidades, descartes e sentimento). Rotas, códigos de erro e a coleção Postman estão em `postman/README.md`.

**Avaliação**: `scripts/eval_us13.py` compara, por consulta, os termos anotados em `data/text_samples/annotations.csv` com os eventos `termo_critico`. Ele grava em `eval.md` a precision, o recall, o F1, os falsos positivos e os falsos negativos (com o motivo de cada um) e os descartes corretos, além de um exemplo de termo detectado e um de negação ignorada para o relatório.

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
