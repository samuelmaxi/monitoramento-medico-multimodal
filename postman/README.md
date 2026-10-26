# Coleções Postman da API

| Arquivo | O que cobre |
| --- | --- |
| `audio-texto.json` | `/health`, termos críticos e sentimento nas transcrições (US13), `/sentiment`, transcrição (US11) e o fluxo áudio → análise |
| `video-pose.json` | Análise de pose em vídeo (US06): enfileirar, status, keypoints, ângulos e feedback |

## Rotas da API

| Método | Rota | O que faz | Precisa no `.env` |
| --- | --- | --- | --- |
| GET | `/health` | Confere se a API está no ar | — |
| POST | `/text/analysis` | Termos críticos e sentimento numa transcrição diarizada (US13): o JSON da US11 ou o formato compacto. Devolve eventos `EventoAchado` (`modality="texto"`) | `AZURE_LANGUAGE_ENDPOINT`, `AZURE_LANGUAGE_KEY`, `PSEUDONYM_KEY` |
| POST | `/audio/analysis` | Áudio da consulta → transcrição (US11) → termos críticos e sentimento na fala do paciente (US13), numa chamada | as duas linhas de US11 e US13 |
| POST | `/sentiment` | Sentimento de um texto avulso | `AZURE_LANGUAGE_ENDPOINT`, `AZURE_LANGUAGE_KEY` |
| POST | `/transcription` | Transcreve um áudio em pt-BR, com diarização e timestamps (US11). Síncrono | `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` (ou `AZURE_SPEECH_ENDPOINT`) |
| POST | `/video/pose` | Enfileira um vídeo para análise de pose (US06) | `PSEUDONYM_KEY` (se enviar IDs de origem) |
| GET | `/video/pose/{job_id}` | Status e resumo do job | — |
| GET | `/video/pose/{job_id}/keypoints` | Keypoints por quadro (JSON) | — |
| GET | `/video/pose/{job_id}/angles` | Ângulos por quadro (CSV) | — |
| GET | `/video/pose/{job_id}/feedback` | Resultado em linguagem natural | — |

A documentação interativa (Swagger) fica em <http://127.0.0.1:8000/docs> com a API no ar.

### `POST /transcription` (US11)

`multipart/form-data`: `audio_file` e, opcionais, os campos que a US13 precisa para usar a transcrição direto.

| Campo | O que é |
| --- | --- |
| `patient_id` | ID de origem ou pseudônimo `pt_…`. Fica como veio no JSON da transcrição; a US13 pseudonimiza ao emitir eventos |
| `recorded_at` | Início da gravação, ISO-8601 UTC (`2026-10-01T13:00:00Z`). Fora de UTC → 422, antes de gravar o áudio |
| `audio_id` | ID curto da consulta (sem dados pessoais). Padrão: o `job_id` (`transcription-<12 hex>`) |
| `medico_falante` | Número do falante (diarização) que é o médico; os demais viram `paciente` em `falas[].papel` |

O áudio e o JSON ficam em `saida/transcricao/<job_id>/` (`TRANSCRIPTION_OUTPUT_DIR`).

### `POST /audio/analysis` (US11 + US13)

Mesmo `multipart/form-data` da transcrição, mas `patient_id`, `recorded_at` e `medico_falante` são obrigatórios. `max_falantes` (padrão 2) e `?detalhes=true` são opcionais. Transcreve, separa a fala do paciente pelo `medico_falante` e roda a análise da US13.

```bash
curl -X POST "http://127.0.0.1:8000/audio/analysis?detalhes=true" \
  -F "audio_file=@consulta_01.wav" -F "patient_id=10000032" \
  -F "recorded_at=2026-10-01T13:00:00Z" -F "medico_falante=1"
```

Resposta: `audio_id`, `patient_id` (pseudônimo), `transcricao_uri`, `transcricao` (o JSON da US11), `eventos` e, com `detalhes`, o sentimento por sentença. Formato, config, credenciais da análise e `PSEUDONYM_KEY` são conferidos antes da chamada ao Speech, para não gastar a transcrição. Os códigos de erro são os das duas rotas: os do Speech (401/403, 429, 502, 504, com o áudio preservado) e os da tabela da US13 abaixo.

### `POST /text/analysis` (US13)

Corpo: o JSON devolvido por `POST /transcription` (transcrito com `medico_falante`, `patient_id` e `recorded_at`) ou o formato compacto abaixo, usado nas amostras de `data/text_samples/`.

```json
{
  "audio_id": "consulta-07",
  "patient_id": "sim-paciente-07",
  "recorded_at": "2026-10-07T13:00:00Z",
  "is_synthetic": true,
  "segments": [
    {"speaker": "medico", "text": "O que aconteceu?", "offset_ms": 500, "duration_ms": 2000},
    {"speaker": "paciente", "text": "Vi sangue nas fezes e estou com tontura.", "offset_ms": 3000, "duration_ms": 4000, "confidence": 0.9}
  ]
}
```

- `audio_id` vira `context.source_id`: só letras, números, `_`, `.` e `-`, sem dados pessoais.
- `patient_id` pode ser o ID de origem: ele é pseudonimizado (`pt_…`) antes de sair.
- `recorded_at` precisa estar em UTC (sufixo `Z`), porque `recorded_at + offset_ms` vira o `timestamp` do evento.
- `speaker`: só as falas com `paciente`/`patient` vão ao Azure AI Language. As do médico (`medico`/`doctor`) entram apenas como contexto da evidência.
- No JSON da US11, o falante de cada fala é o `papel`. Sem `medico_falante` não há papel, e a rota responde 422 em vez de mandar a fala do médico para a análise.
- `?detalhes=true` inclui as entidades extraídas com as asserções, os descartes com o motivo e o sentimento por sentença.

Resposta 200:

```json
{
  "audio_id": "consulta-07",
  "patient_id": "pt_7c52bda40f182aee0fa3b7d2",
  "eventos": [
    {"modality": "texto", "event_type": "termo_critico", "severity": "alta",
     "evidence": {"summary": "Paciente relata sangramento: “sangue nas fezes” (afirmado, sem negação)", "...": "..."}}
  ]
}
```

Os eventos também são publicados no barramento local (`EVENTOS_JSONL`, padrão `saida/eventos.jsonl`), e o stub de alertas registra os de severidade alta no log da API.

| Status | Quando |
| --- | --- |
| 422 | Transcrição inválida: campo ausente ou nulo, `audio_id` fora do padrão, `recorded_at` fora de UTC ou JSON da US11 sem `medico_falante` |
| 503 | Falta `AZURE_LANGUAGE_ENDPOINT`/`AZURE_LANGUAGE_KEY` ou `PSEUDONYM_KEY` |
| 401 | A Azure recusou a chave |
| 429 | A Azure continuou limitando as requisições depois das tentativas (`azure.retry` no config) |
| 502 | Outra falha da Azure, inclusive job do health sem resposta em `azure.health_timeout_s` |

A análise é síncrona. A primeira chamada de um texto leva alguns segundos; as repetições vêm do cache (`saida/cache_azure/language/`). O config usado é `audio/text/config.yaml` (outro caminho: `TEXT_CONFIG_PATH`).

## Como testar

### 1. Subir a API

```bash
cp .env.sample .env        # preencha as variáveis da tabela acima
uv sync                    # instala as dependências do pyproject.toml, inclusive o uvicorn
uv run uvicorn app.main:app --reload
curl http://127.0.0.1:8000/health    # {"status":"ok"}
```

### 2a. No aplicativo Postman

1. **Import** → selecione `postman/audio-texto.json`.
2. Se a API não estiver em `http://127.0.0.1:8000`, ajuste a variável `baseUrl` na aba **Variables** da coleção.
3. Abra uma requisição e clique em **Send**. A aba **Test Results** mostra as verificações de cada uma.
4. Para rodar tudo: botão **Run** na coleção (Collection Runner).

Na pasta **US11 + US13 - Áudio da consulta**, escolha um arquivo no campo `audio_file` e ajuste `medico_falante` antes de enviar. **Transcrever áudio** guarda o JSON na variável `transcricao`, que **Analisar a transcrição (US13)** envia para `/text/analysis`. **Áudio ponta a ponta** faz as duas etapas numa chamada.

### 2b. Pela linha de comando (Postman CLI)

```bash
postman collection run postman/audio-texto.json \
  -i "Health" -i "US13 - Termos críticos e sentimento" -i "Sentimento"
```

A pasta de áudio fica de fora porque precisa de um arquivo de áudio selecionado. A mensagem final sobre `postman login` só diz respeito a publicar o resultado na nuvem; ela não indica falha.

### 2c. Com curl

```bash
curl -X POST "http://127.0.0.1:8000/text/analysis?detalhes=true" \
  -H "Content-Type: application/json" --data @data/text_samples/consulta-07.json
```

## O que cada requisição da US13 verifica

| Requisição | Entrada | Esperado |
| --- | --- | --- |
| Termo crítico afirmado | Consulta simulada 07 | `termo_critico` de sangramento (alta) e tontura (media); sem `detalhes` por padrão |
| Negação ignorada | Consulta 02 ("não sinto dor no peito", "nego falta de ar") | Nenhum `termo_critico` |
| Sintoma de terceiro ignorado | Consulta 03, sem a fala da irmã (pai com infarto, mãe com AVC) | Nenhum `termo_critico` |
| Com detalhes | Consulta 02 com `?detalhes=true` | Descarte com `certeza=negative` e resumo do sentimento |
| Erro 422 | `recorded_at` com `-03:00` | 422 citando UTC |

Todas checam que os eventos têm `modality="texto"` e o paciente pseudonimizado.

## Limitações conhecidas

- **US13 em português**: o modelo `pt` do Text Analytics for health nem sempre marca hipótese e terceiro. Por exemplo, "minha irmã tem convulsões" e "se a falta de ar piorar" viram achado. Por isso a requisição de terceiro usa a consulta 03 sem a fala da irmã. Medições em `outputs/text_analysis/*/eval.md` (`scripts/eval_us13.py`).
- **US11**: a diarização numera os falantes na ordem em que aparecem, mas não diz qual é o médico. Por isso `medico_falante` precisa ser informado; se for o número errado, a fala do médico vai para a análise e a do paciente fica de fora.
