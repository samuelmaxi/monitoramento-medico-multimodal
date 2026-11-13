# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Academic (Tech Challenge) system for multimodal medical monitoring. Each modality (video, audio/text, vital signs, prescriptions, patient movement) has its own detector that emits findings in a single event contract (`EventoAchado`); an alert engine consumes those findings and notifies the care team. Azure AI Speech/Language is used only for transcription, clinical NLP and sentiment; everything else runs locally.

Work is organized by user story (`US05`, `US07`, `US15`…). Code, identifiers, comments, docs and commit messages are in **Brazilian Portuguese** — match that. Each US has a DoD report in `docs/relatorio_usNN_dod.md` that records the protocol, thresholds and results; update it when you change that module's behavior.

## Commands

Python 3.13+, managed with `uv`. Run everything from the repo root (modules rely on `pythonpath = ["."]` and scripts do `sys.path.insert` of the root).

```bash
uv sync                                    # install (dev group includes pytest, ruff, httpx, jsonschema)
cp .env.sample .env                        # then fill in what the module you touch needs
uv run uvicorn app.main:app --reload --reload-dir app --reload-dir video --reload-dir contratos
                                           # restrict --reload dirs, otherwise it also watches .venv
uv run ruff check .                        # line-length 100, py313, rules E,F,W,I,B,C4,UP,SIM,RUF
```

### Tests

`pytest` is configured with `testpaths = ["tests"]`, so **`audio/tests` and `video/tests` are NOT collected by default** — pass them explicitly.

```bash
uv run pytest                              # tests/ only
uv run pytest audio/tests video/tests      # the two test dirs that live beside their packages (US13 text, US06 pose)
uv run pytest tests/audio                  # US10/US12/US14: synthetic audio in tmp_path, no network (a few tests use the real YAMNet if models/audio/ is populated)
uv run pytest tests/alertas/test_motor.py::test_nome   # single test
uv run pytest tests/sinais                 # one module (also: movimentacao, prescricoes, video, alertas, contratos, transcricao)
```

- `tests/test_smoke.py` calls the **real Azure** APIs and is skipped unless the `AZURE_*` vars are in the environment (see `tests/conftest.py`). Everything else uses fakes (fake detector/pose estimator, `EmissorMemoria`, `NotificadorFake`, in-memory alert store) and needs no network, weights or credentials.
- Most tests set `PSEUDONYM_KEY` through a fixture. Code that pseudonymizes IDs raises `ChavePseudonimizacaoAusente` without it, so set it (≥ 32 bytes) when running CLIs or the API manually.

### Running modules

Modules are normally run as `python -m <pkg>.run` or via `scripts/`. Examples:

```bash
uv run python -m video.pose.run --config video/pose/config.yaml --input <video>      # US06 pose (OpenPose weights: scripts/download_openpose_models.sh)
uv run python scripts/rodar_us07_video.py config/exemplo_us07.json --max-frames 100  # US07 YOLOv8 + ROIs
uv run python -m video.desvios.run --perfil <perfil> --pose <json>                   # US08
uv run python -m audio.text.run --input data/text_samples --config audio/text/config.yaml   # US13 (needs Azure Language)
uv run python -m audio.ingestao.run --input <pasta>                                  # US10 convert audio to WAV 16 kHz mono (one bad file does not stop the batch)
uv run python scripts/baixar_modelos_audio.py                                        # YAMNet ONNX -> models/audio/ (optional, SHA-256 pinned)
uv run --env-file .env python -m audio.vocal.run --audio <wav> --patient-id <id> --recorded-at <ISO UTC> [--transcrever --medico-falante N]   # US12/US14
uv run python scripts/avaliar_us14.py                                                # US14 recall on injected synthetic cases (~2 min)
uv run python scripts/rodar_us15.py                                                  # US15 replay (after scripts/baixar_sinais.py)
uv run python -m prescricoes.run --baixar                                            # US16
uv run --env-file .env python -m movimentacao.run mimic                              # US17
uv run python scripts/simular_alertas.py [--dedup|--escalonar|--todos]               # US19 with synthetic events
uv run python -m contratos.validar <arquivo.jsonl|json>                              # validate events against the schema
uv run python -m contratos.schema                                                    # regenerate the JSON Schema
```

`scripts/avaliar_usNN.py` run each story's evaluation. `scripts/get_content_script.py` downloads sample videos from Google Drive into `conteudos/` (needs `GOOGLE_DRIVE_FOLDER_URL`; `gdown` nests a folder, e.g. `conteudos/conteudos/videos/`). Root `main.py` is just a "Hello World" sanity check.

## Architecture

### The event contract is the integration point

`contratos/` is the spine of the repo. **Modules never call each other**; they only publish `EventoAchado` (pydantic, `contratos/evento.py`) and consume events.

- `contratos/catalogo.py` is the single source of truth for `Modalidade`, `Severidade` (info/baixa/media/alta, IEC 60601-1-8) and the allowed `event_type` per modality. A new event type means editing `TIPOS_DE_EVENTO` **and** regenerating `contratos/schema/evento_achado.v1.schema.json` (`python -m contratos.schema`). Fields are strictly validated (UTC timestamps, `model_version` as `name@version`, 256 KB max, a PII guard on free text).
- Heavy evidence (frames, audio snippets, reports) stays on disk; the event holds only an artifact URI.
- `patient_id` is always a pseudonym (HMAC-SHA256 with `PSEUDONYM_KEY`, `contratos/pseudonimizacao.py`) — never put a raw ID in an event or on disk.
- Detectors take an `Emissor` (Protocol) as a parameter and don't know which one it is: `EmissorMemoria` (tests), `EmissorJsonl` (isolated CLI runs) or `BarramentoLocal` (pipeline). Use `EventoAchado.criar(...)`, not raw dicts.
- `BarramentoLocal` (`contratos/emissor.py`) is a synchronous, ordered in-process bus: it validates, appends to `saida/eventos.jsonl` (`EVENTOS_JSONL`), then delivers to subscribers filtered by `filtro(modalidades=..., severidade_minima=...)`. A subscriber that raises is recorded in `falhas` and does not stop the others; a subscriber that publishes enqueues rather than recursing. `app/barramento.py::get_barramento()` is the process-wide singleton the alert engine subscribes to — a router must publish to *that* instance, or the alert engine never sees the events. Currently only `text_analysis` does; `app/routers/prescricoes.py` defines its own `lru_cache`d `get_barramento()` (a second bus, so its events are written to the JSONL but never reach alerts), and the vital-signs and critical-areas routers return events in the response without publishing them (the pose router emits none, by design).
- `sem_achados` events exist so fusion can tell "modality present and normal" from "modality absent".

### Detector packages (each emits `EventoAchado`)

| Package | US | Notes |
| --- | --- | --- |
| `video/` | US06–US08 | `video/pose` (OpenPose → angles → rule-based feedback), top-level `video/*.py` (YOLOv8 + ByteTrack + critical-area ROIs, US07), `video/desvios` (pose × ROIs vs. expected pattern per video profile, US08) |
| `app/video/` | US05 | **Different from `video/`**: frame sampling + face anonymization (YuNet) pipeline, no detection yet |
| `audio/text/` | US13 | Azure Text Analytics for health on **patient speech only** + assertion filter + critical-term list. Transcription itself (US11) lives in `app/transcription_*` |
| `audio/ingestao/` | US10 | `soundfile` (WAV/FLAC/OGG-Opus) or ffmpeg fallback → `convertido.wav` (PCM 16 kHz mono) + `features.wav` (normalized, long silences removed) + metadata. Original never touched |
| `audio/vocal/` | US12, US14 | Praat/parselmouth biomarkers per patient segment, YAMNet (ONNX) respiratory events, per-patient baseline, rule-based classes → `EventoAchado`. `audio/configuracao.py` and `audio/sinal.py` are shared helpers |
| `sinais/` | US15 | Per-patient stateful `DetectorSinais`: cleaning → NEWS2 → robust-z/trend vs. patient baseline. Local numpy/pandas only |
| `prescricoes/` | US16 | MIMIC-IV Demo prescription rules (dose jump, duplication, abrupt stop, ISMP high-alert) |
| `movimentacao/` | US17 | Movement series from video + MIMIC nursing charts → immobility, bed exit, fall, agitation/reduction |
| `alertas/` | US19 | Modality-agnostic alert engine (see below) |

Thresholds live in versioned YAML/JSON under `config/` and `audio/text/*.yaml`, `video/pose/config.yaml` — not in code. Change limits there.

### Alert engine (`alertas/`)

Subscribes to the bus with `severidade_minima=baixa` and knows no modality. Pipeline: `motor.py` (rules from `config/alertas_regras.yaml`, priority, dedup window in `dedup.py`, escalation timer) → `despachante.py` (async queue, retries per channel) → `notificadores/` (SMTP, webhook, fake) with acknowledgement tokens (`ack.py`, `ALERTA_ACK_SECRET`) and an audit trail. Storage is pluggable (`armazenamento/`: `jsonl` default, `memoria`, `cosmos`; selected by `ALERTA_STORE`, built by `fabrica.construir`). `app/main.py`'s lifespan wires the service to the bus and runs its worker/timer. Time is injected in tests (see `Relogio` in `tests/alertas/conftest.py`) — do not `sleep`.

### API (`app/`)

FastAPI app; each router wraps one flow (transcription, text/audio analysis, sentiment, video pose, video critical areas, vital signs, prescriptions, alerts). Notes that are easy to miss:

- Video endpoints are **asynchronous jobs** (`app/pose_jobs.py`, `app/areas_jobs.py`): `POST` returns `202` + `job_id`, jobs run one at a time, results are persisted as `job.json` under `outputs/pose/api/` / `saida/video/api/`. Uploaded video is deleted after the job; jobs in flight when the API restarts become `FAILED`.
- Vital-sign baselines are held **in process memory**; run a single worker.
- Azure clients/retry/error translation: `app/azure_clients.py`, `app/errors.py` (401/403/429 mapping). Transcription (US11) is Azure fast transcription via REST in `app/transcription_*.py`, with SHA-256 caching under `saida/`; Azure responses are cached in `saida/cache_azure/` — delete to force a re-call.
- Env vars are read per module (`app/config.py`, `alertas/config.py`, `.env.sample` is the reference).

## Gotchas

- **Docs are partly aspirational.** `docs/arquitetura/README.md` describes a target state (Azure `DefaultAzureCredential`, `brazilsouth`, `scripts/rodar_demo.py`, a fusion module `fusao_alertas/`, ACS Email) — none of that exists yet; the code authenticates with keys from `.env` (`AZURE_SPEECH_KEY`, `AZURE_LANGUAGE_KEY`…), there is no fusion package (only `Modalidade.FUSAO` in the catalog), and `README.md` still has stale parts (the install section lists only `gdown`/`python-dotenv`, the Terraform file list, an `audio/text/alerts.py` stub that no longer exists). Check the code before trusting those descriptions.
- Gitignored data dirs: `conteudos/`, `dados/` (MIMIC-IV Demo, REHAB24-6…), `outputs/`, `saida/` (a few result files are force-tracked), and `data/*` except `data/text_samples/` and `data/eval/`. Scripts that need datasets download them (`scripts/baixar_*.py`); don't expect them to be present.
- `yolov8n.pt` and `app/video/models/face_detection_yunet_2023mar.onnx` are tracked weights; OpenPose weights are not (`scripts/download_openpose_models.sh`) and `POST /video/pose` returns 503 without them.
- Sentiment language code is `pt-BR` (plain `pt` is treated as pt-PT), while Text Analytics for health only accepts `pt`.
- **Audio (US10/12/14).** All thresholds live in `audio/vocal/config.yaml`, each tagged `[MDVP]` (from the US12 text), `[PRAAT]` or `[EXP]` (experimental, **not clinically validated**). Pauses and speech rate are measured on `convertido.wav`, voice quality on the `features.wav` equivalent (mapped back through `CopiaFeatures.indice`) — silence removal would erase the pauses. Results are always "indicativo para avaliação clínica, não diagnóstico". MDVP jitter/shimmer/HNR limits are for clinical-quality recordings: lossy audio (Opus/WhatsApp, MP3) trips all three, so events carry `audio_comprimido`. Do **not** add an `alertas_regras.yaml` rule for audio: a rule overrides priority and would defeat the 2-criteria=média / 3=alta logic. `audio_id` becomes a folder name and must start with a letter or digit (the contract pattern allows `..`). `app/routers/audio_vocal.py` publishes to the shared `get_barramento()`.
- `opencv-python` and `opencv-python-headless` are both in `pyproject.toml`; the pinned `video/requirements.txt` / `audio/requirements.txt` are per-module installs for people not using `uv`.
- Infrastructure is Terraform in `terraform/` (Azure Speech/Language F0 in `eastus`, Cosmos DB for alert storage); `terraform.tfvars` is not committed.
