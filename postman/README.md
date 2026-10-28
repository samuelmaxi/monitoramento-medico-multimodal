# Coleções Postman da API

| Arquivo | O que cobre |
| --- | --- |
| `audio-texto.json` | `/health`, termos críticos e sentimento nas transcrições (US13), `/sentiment`, transcrição (US11) e o fluxo áudio → análise |
| `video-pose.json` | Análise de pose em vídeo (US06): enfileirar, status, keypoints, ângulos e feedback |
| `prescricoes.json` | Anomalias na evolução de prescrições (US16): um exemplo de cada anomalia, controles sem anomalia, erros e pacientes reais do MIMIC-IV Demo |

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
| POST | `/prescricoes/analise` | Ordens de uma internação → eventos `EventoAchado` (`modality="prescricoes"`): mudança abrupta de dose, duplicidade, suspensão abrupta e alta vigilância (US16) | `PSEUDONYM_KEY` (se enviar IDs de origem) |
| GET | `/prescricoes/mimic/{subject_id}` | A mesma análise nas prescrições reais de um paciente do MIMIC-IV Demo | `PSEUDONYM_KEY`; tabelas em `dados/mimic-iv-demo` |
| GET | `/prescricoes/config` | Limiares e lista ISMP em uso | — |

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

### `POST /prescricoes/analise` (US16)

Corpo: as ordens de uma internação, com os campos das tabelas `prescriptions` e `pharmacy` do MIMIC. Todos os horários vão em UTC.

```json
{
  "patient_id": "sim-paciente-16a",
  "encounter_id": "sim-paciente-16a-int1",
  "admissao": "2026-10-01T10:00:00Z",
  "alta": "2026-10-12T14:00:00Z",
  "is_synthetic": true,
  "prescricoes": [
    {"ordem_id": "enox-1", "medicamento": "Enoxaparin Sodium", "dose": "40", "unidade": "mg", "via": "SC",
     "frequencia": "QPM", "doses_24h": 1, "inicio": "2026-10-01T22:00:00Z", "fim": "2026-10-03T11:00:00Z"},
    {"ordem_id": "enox-2", "medicamento": "Enoxaparin Sodium", "dose": "100", "unidade": "mg", "via": "SC",
     "frequencia": "Q12H", "doses_24h": 2, "inicio": "2026-10-03T12:00:00Z", "fim": "2026-10-07T20:00:00Z"}
  ]
}
```

| Campo | O que é |
| --- | --- |
| `patient_id`, `encounter_id` | ID de origem ou pseudônimo `pt_…`/`enc_…`. O ID de origem é pseudonimizado antes de sair |
| `admissao`, `alta`, `obito` | `admissao` tem como padrão o início da primeira ordem. Sem `alta`, a suspensão abrupta não é avaliada, porque não dá para separar a suspensão do fim da internação |
| `prescricoes[].dose` | Texto ou número: `"40"`, `"2-4"` (faixa: compara o máximo), `"25,000"` |
| `prescricoes[].unidade` | Normalizada antes de comparar: mcg/g → mg, L → mL. `UNIT`, `mEq` e `mmol` ficam como estão |
| `prescricoes[].frequencia` | `PRN`, `ONCE`, `TITRATE TO`, `ASDIR` e escala móvel (`escala_movel: true`) não têm dose fixa e ficam fora da mudança de dose |
| `prescricoes[].doses_24h` | Dá a dose diária. Sem ela, só compara doses com a mesma frequência |
| `prescricoes[].apresentacao` | `prod_strength`. O KCl só é de alta vigilância concentrado (`2mEq/mL`) |
| `prescricoes[].status` | `Inactive (Due to a change order)` marca a ordem substituída, que se fecha no início da seguinte |

- `?detalhes=true` inclui `normalizadas`: princípio ativo, dose e unidade canônicas, dose diária e tipo de frequência de cada ordem.
- `?publicar=false` não grava no barramento local. O padrão é `true`, como nas outras rotas: os eventos vão para `EVENTOS_JSONL` e seguem para fusão e alertas.

Resposta 200: `patient_id`, `encounter_id`, `versao_limiares`, `prescricoes_analisadas`, `total_anomalias`, `por_tipo` e `eventos`. Cada evento traz a justificativa em `evidence.summary` e os valores antes × depois em `evidence.features` (`antes_dose_diaria`, `depois_medicamento` etc.). Internação sem anomalia devolve um `sem_achados` (info).

`GET /prescricoes/mimic/{subject_id}` faz a mesma análise nas prescrições reais de um paciente do demo, com `?hadm_id=` opcional. As tabelas são lidas de `dados/mimic-iv-demo` (ou de `PRESCRICOES_DADOS_DIR`) e baixadas uma vez com `uv run python -m prescricoes.run --baixar`.

| Status | Quando |
| --- | --- |
| 422 | Horário fora de UTC, `prescricoes` vazio, fim antes do início, `ordem_id` repetido, alta antes da admissão |
| 404 | `subject_id` fora do demo, ou `hadm_id` de outro paciente |
| 503 | Falta `PSEUDONYM_KEY`, as tabelas do MIMIC não foram baixadas, ou a config está inválida |

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
# US16: roda sem Azure; a pasta do MIMIC precisa das tabelas baixadas
postman collection run postman/prescricoes.json
```


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

## O que cada requisição da US16 verifica

| Requisição | Entrada | Esperado |
| --- | --- | --- |
| Limiares e lista ISMP em uso | `GET /prescricoes/config` | Dose ±50% em 24 h; classes insulina, anticoagulante, opioide e KCl concentrado |
| Mudança abrupta de dose | Enoxaparina 40 mg/dia → 100 mg a cada 12 h | `mudanca_abrupta_dose` média, +400%, `antes_dose_diaria=40` e `depois_dose_diaria=200` |
| Duplicidade terapêutica | Oxicodona+paracetamol PRN com paracetamol 1 g TID | `duplicidade_terapeutica` com `principio_ativo=acetaminophen` |
| Suspensão abrupta | Metoprolol 25 mg TID por 4 dias, parado 6 dias antes da alta | `suspensao_abrupta` no fim da última ordem |
| Alta vigilância | Pantoprazol e KCl 2 mEq/mL IV | Um único `medicamento_alta_vigilancia`, `potassio_concentrado`, severidade alta |
| Sem achados | Prednisona 40 → 30 mg e furosemida 40 → 50 mg (±25%) | Só `sem_achados` (info) |
| Unidades normalizadas | Levotiroxina 100 mcg → 0,025 mg, `?detalhes=true` | As duas ordens em mg; queda de 75% |
| Erros 422 | Horário com `-03:00`; ordem com fim antes do início | 422 citando UTC / "antes do início" |
| Pacientes reais do MIMIC | `subject_id` 10008454; 10002428 com `hadm_id` 28676446; 99999999 | Enoxaparina +400%; paracetamol duplicado; 404 |

Todas checam `modality="prescricoes"`, o paciente pseudonimizado e a `versao_limiares` nos eventos. A coleção vem de `scripts/gerar_postman_us16.py`, e `tests/prescricoes/test_api.py` envia os mesmos corpos à API. Mudou um exemplo, gere a coleção de novo com `uv run python scripts/gerar_postman_us16.py`. A variável `publicar` da coleção vem como `false`, para que rodar a coleção não encha `saida/eventos.jsonl` de eventos de teste.

## Limitações conhecidas

- **US13 em português**: o modelo `pt` do Text Analytics for health nem sempre marca hipótese e terceiro. Por exemplo, "minha irmã tem convulsões" e "se a falta de ar piorar" viram achado. Por isso a requisição de terceiro usa a consulta 03 sem a fala da irmã. Medições em `outputs/text_analysis/*/eval.md` (`scripts/eval_us13.py`).
- **US11**: a diarização numera os falantes na ordem em que aparecem, mas não diz qual é o médico. Por isso `medico_falante` precisa ser informado; se for o número errado, a fala do médico vai para a análise e a do paciente fica de fora.
