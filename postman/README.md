# Coleções Postman da API

| Arquivo | O que cobre |
| --- | --- |
| `audio-texto.json` | `/health`, termos críticos e sentimento nas transcrições (US13), `/sentiment` e transcrição (US11) |
| `video-pose.json` | Análise de pose em vídeo (US06): enfileirar, status, keypoints, ângulos e feedback |

## Rotas da API

| Método | Rota | O que faz | Precisa no `.env` |
| --- | --- | --- | --- |
| GET | `/health` | Confere se a API está no ar | — |
| POST | `/text/analysis` | Termos críticos e sentimento numa transcrição diarizada (US13). Devolve eventos `EventoAchado` (`modality="texto"`) | `AZURE_LANGUAGE_ENDPOINT`, `AZURE_LANGUAGE_KEY`, `PSEUDONYM_KEY` |
| POST | `/sentiment` | Sentimento de um texto avulso | `AZURE_LANGUAGE_ENDPOINT`, `AZURE_LANGUAGE_KEY` |
| POST | `/transcription` | Envia um áudio e cria o job de transcrição (US11) | `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`, `AZURE_STORAGE_CONNECTION_STRING` |
| GET | `/transcription/{job_name}` | Resultado da transcrição | idem |
| POST | `/video/pose` | Enfileira um vídeo para análise de pose (US06) | `PSEUDONYM_KEY` (se enviar IDs de origem) |
| GET | `/video/pose/{job_id}` | Status e resumo do job | — |
| GET | `/video/pose/{job_id}/keypoints` | Keypoints por quadro (JSON) | — |
| GET | `/video/pose/{job_id}/angles` | Ângulos por quadro (CSV) | — |
| GET | `/video/pose/{job_id}/feedback` | Resultado em linguagem natural | — |

A documentação interativa (Swagger) fica em <http://127.0.0.1:8000/docs> com a API no ar.

### `POST /text/analysis` (US13)

Corpo: a transcrição no formato da US11.

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
| 422 | Transcrição inválida: campo ausente, `audio_id` fora do padrão ou `recorded_at` fora de UTC |
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

Na pasta **US11 - Transcrição**, escolha um arquivo no campo `audio_file` do **Enviar áudio** antes de enviar. O `job_name` devolvido fica salvo na variável da coleção e é usado pelo **Resultado da transcrição**.

### 2b. Pela linha de comando (Postman CLI)

```bash
postman collection run postman/audio-texto.json \
  -i "Health" -i "US13 - Termos críticos e sentimento" -i "Sentimento"
```

A pasta de transcrição fica de fora porque precisa de um arquivo de áudio selecionado. A mensagem final sobre `postman login` só diz respeito a publicar o resultado na nuvem; ela não indica falha.

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
- **US11**: `GET /transcription/{job_name}` usa `recognize_once`, que devolve só a primeira frase do áudio, sem separar os falantes. A saída ainda não está no formato que `/text/analysis` recebe.
- **US11**: sem `AZURE_STORAGE_CONNECTION_STRING`, o `POST /transcription` responde 500 com a mensagem da variável ausente.
