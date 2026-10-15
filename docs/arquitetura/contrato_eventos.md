# Contrato de eventos: `EventoAchado` v1.0.0

Formato único de todo achado emitido pelos módulos de vídeo, áudio, texto, sinais vitais, prescrições, movimentação e fusão.

| Onde                                            | O quê                                                                     |
| ----------------------------------------------- | ------------------------------------------------------------------------- |
| `contratos/evento.py`                           | Modelo pydantic, a fonte da verdade                                       |
| `contratos/catalogo.py`                         | Modalidades, severidades e `event_type` permitidos por modalidade         |
| `contratos/schema/evento_achado.v1.schema.json` | JSON Schema 2020-12 gerado do modelo, para consumidores fora do Python    |
| `contratos/exemplos/`                           | Um exemplo válido por modalidade, todos do mesmo paciente de demonstração |
| `tests/contratos/`                              | Testes do contrato, do schema e dos emissores                             |

## Campos

| Campo               | Tipo               | Obrigatório                      | Regra                                                                                                                          |
| ------------------- | ------------------ | -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `schema_version`    | `"1.0.0"`          | sim (preenchido automaticamente) | Versão do contrato                                                                                                             |
| `event_id`          | UUID               | sim (`criar()` gera)             | Chave de idempotência (deduplicação nos alertas e, em produção, `MessageId` no Service Bus)                                     |
| `patient_id`        | string             | sim                              | Pseudônimo `pt_<24 hex>` gerado por `pseudonimizar_paciente()`. IDs crus são recusados                                         |
| `modality`          | enum               | sim                              | `video`, `audio`, `texto`, `sinais_vitais`, `prescricoes`, `movimentacao`, `fusao`                                             |
| `event_type`        | string             | sim                              | Precisa estar no catálogo **da modalidade** (tabela abaixo)                                                                    |
| `timestamp`         | ISO-8601 UTC       | sim                              | Quando o fenômeno ocorreu. Aceita `Z` ou `+00:00`, emite sempre `2026-09-26T17:02:00.000Z`. Sem fuso ou fora de UTC é recusado |
| `detected_at`       | ISO-8601 UTC       | sim (`criar()` gera)             | Quando o módulo emitiu                                                                                                         |
| `ingested_at`       | ISO-8601 UTC       | não                              | Quando o dado bruto entrou no sistema. Base da latência medida na US19                                                         |
| `window`            | `{start, end}` UTC | não                              | Intervalo coberto (desvio com início/fim, janela de sinais, imobilidade). `timestamp` precisa estar dentro dela                |
| `score`             | float              | sim                              | Entre 0 e 1: intensidade ou confiança normalizada da anomalia. NaN e infinito são recusados                                    |
| `severity`          | enum               | sim                              | `info`, `baixa`, `media`, `alta`, alinhado à IEC 60601-1-8 (3 prioridades mais o sinal de informação)                          |
| `evidence`          | objeto             | sim                              | Explicação do achado (ver abaixo)                                                                                              |
| `model_version`     | string             | sim                              | `<componente>@<versão>` do módulo emissor, ex. `anomalias-sinais@0.1.0`                                                        |
| `context`           | objeto             | não                              | `encounter_id` (`enc_<24 hex>`), `bed_id`, `source_id`, `procedure_type`, usados pelo alerta                                   |
| `related_event_ids` | lista de UUID      | obrigatório na fusão             | Eventos que originaram este. Um evento não pode se referenciar                                                                 |
| `is_synthetic`      | bool               | não (padrão `false`)             | `true` para anomalias injetadas em teste                                                                                       |

Campos fora dessa lista são recusados (`extra = forbid`). O objetivo é que nenhum módulo invente campo por conta própria.

### `evidence`

| Campo        | Regra                                                                                                                                                                                                                                                                                             |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `summary`    | Obrigatório. Frase legível em pt-BR (3 a 500 caracteres). É o texto que chega à equipe médica                                                                                                                                                                                                     |
| `features`   | Valores observados que levaram à decisão, ex. `{"spo2_pct": 92, "news2_total": 6}`. Aceita número, texto (até 200 caracteres), booleano ou nulo                                                                                                                                                   |
| `thresholds` | Limiares aplicados, ex. `{"news2_emergencia_total": 7}`                                                                                                                                                                                                                                           |
| `models`     | Modelos e serviços de base, ex. `{"openpose": "BODY_25", "azure-speech": "pt-BR"}`                                                                                                                                                                                                                   |
| `artifacts`  | Até 20 referências com `kind`, `media_type` e `start_ms`/`end_ms` relativos à mídia. `uri` é um caminho relativo à raiz do repositório (`saida/video/fisio-003/frame.jpg`) ou uma URI `https://` (ex. Azure Blob Storage; `s3://` ainda é aceito por compatibilidade). Caminhos absolutos são recusados, porque só funcionam na máquina de quem gerou o evento |

O contrato bloqueia padrões de **CPF e e-mail** em `summary` e nas `features` de texto. É um guarda-corpo, não substitui revisão.

### Regras entre campos

- `event_type` pertence à `modality`.
- `sem_achados` exige `severity = info`.
- `modality = fusao` exige ao menos um `related_event_ids`.
- `timestamp` precisa estar dentro de `window`, quando houver janela.
- O evento serializado tem no máximo **256 KB**. Evidências maiores ficam em arquivo e entram como caminho. É o mesmo limite de mensagem do Azure Service Bus Standard, então o contrato não muda se o barramento for para a nuvem. O JSON Schema não consegue expressar essa regra; só o pydantic a verifica.

## Catálogo de `event_type`

| Modalidade            | `event_type`                                                                                                 | US   |
| --------------------- | ------------------------------------------------------------------------------------------------------------ | ---- |
| `video`               | `postura_fora_da_faixa`, `assimetria_movimento`, `mudanca_brusca_ou_queda`, `presenca_indevida_area_critica` | US08 |
| `video`               | `entrada_area_critica`, `saida_area_critica`                                                                 | US07 |
| `audio`               | `fadiga_vocal`, `dificuldade_respiratoria`, `possivel_disartria`                                             | US14 |
| `audio`               | `evento_respiratorio`, `desvio_baseline_vocal`                                                               | US12 |
| `texto`               | `termo_critico`, `sentimento_negativo`                                                                       | US13 |
| `sinais_vitais`       | `news2_resposta_urgente`, `news2_emergencia`, `desvio_baseline`, `tendencia_deterioracao`                    | US15 |
| `prescricoes`         | `mudanca_abrupta_dose`, `duplicidade_terapeutica`, `suspensao_abrupta`, `medicamento_alta_vigilancia`        | US16 |
| `movimentacao`        | `imobilidade_prolongada`, `saida_do_leito`, `queda`, `agitacao_subita`, `reducao_mobilidade`                 | US17 |
| `fusao`               | `risco_multimodal`                                                                                           | US18 |
| todas, exceto `fusao` | `sem_achados`                                                                                                | —    |

Emitir `sem_achados` ao terminar uma análise sem anomalias permite que a fusão distinga "modalidade presente e normal" de "modalidade ausente" (US18).

Para criar um tipo novo:

1. Adicione o tipo em `TIPOS_DE_EVENTO` (`contratos/catalogo.py`).
2. Rode `python -m contratos.schema`.
3. Faça commit do schema atualizado. O teste `test_arquivo_de_schema_esta_em_dia` falha se esquecer.

## `score` × `severity`

São dois campos independentes, cada um com uma pergunta:

- `score` responde "quão forte é o sinal", numa escala de 0 a 1.
- `severity` responde "quão urgente é para a equipe", pela regra clínica do módulo. Exemplos: NEWS2 ≥ 7 → `alta`; opioide novo → `media`; queda noturna → `alta`.

Módulos sem regra clínica própria podem usar `severidade_sugerida(score)`, que classifica assim: `< 0,25` é info, `< 0,5` é baixa, `< 0,75` é média e o restante é alta.

O motor de alertas considera `media` e `alta` (`evento.alertavel`).

## Como emitir (dentro de um módulo)

O módulo recebe um `Emissor` por parâmetro e não sabe qual é. Assim o mesmo código roda no teste, isolado ou no pipeline completo.

```python
from contratos import Emissor, EventoAchado, Modalidade, Severidade, pseudonimizar_paciente

def detectar_news2(janela, emissor: Emissor) -> None:
    ...
    emissor.emitir(EventoAchado.criar(
        patient_id=pseudonimizar_paciente(subject_id),   # lê PSEUDONYM_KEY do ambiente
        modality=Modalidade.SINAIS_VITAIS,
        event_type="news2_emergencia",
        timestamp=instante_utc,                          # datetime com tzinfo=timezone.utc
        ingested_at=instante_ingestao_utc,
        score=0.92,
        severity=Severidade.ALTA,
        evidence={
            "summary": "NEWS2 = 8 (SpO2 89%, FC 128 bpm): avaliação de emergência",
            "features": {"news2_total": 8, "spo2_pct": 89, "fc_bpm": 128},
            "thresholds": {"news2_emergencia_total": 7},
        },
        model_version="anomalias-sinais@0.1.0",
        context={"bed_id": "UTI-07"},
    ))
```

| Emissor                    | Uso                                                                         |
| -------------------------- | --------------------------------------------------------------------------- |
| `BarramentoLocal(caminho)` | Pipeline completo. Valida, grava no JSONL e entrega em ordem a quem assinou |
| `EmissorJsonl(caminho)`    | Rodar um módulo isolado. Só grava uma linha por evento                      |
| `EmissorMemoria()`         | Testes                                                                      |

### Barramento local: como a fusão e os alertas assinam

```python
from contratos import BarramentoLocal, filtro

barramento = BarramentoLocal.do_ambiente()   # grava em EVENTOS_JSONL (padrão saida/eventos.jsonl)
barramento.assinar("fusao", fusao.receber, filtro(exceto_modalidades=["fusao"]))
barramento.assinar("alertas", alertas.receber, filtro(severidade_minima="media"))

detectar_news2(janela, emissor=barramento)   # os detectores publicam no barramento
```

Garantias do barramento:

- **Ordem.** Os eventos são entregues na ordem de publicação.
- **Publicação durante a entrega.** A fusão pode publicar `risco_multimodal` dentro do próprio callback. O novo evento entra na fila e é entregue depois, sem recursão.
- **Isolamento de falhas.** Se um assinante levantar exceção, a falha fica em `barramento.falhas` e os demais continuam recebendo. Um bug na fusão não impede um alerta de NEWS2.
- **Validação antes da gravação.** Evento fora do contrato é recusado antes de ir para o JSONL.

Variáveis de ambiente:

| Variável        | Valores                                                                                                   |
| --------------- | --------------------------------------------------------------------------------------------------------- |
| `EVENTOS_JSONL` | Padrão `saida/eventos.jsonl`                                                                              |
| `PSEUDONYM_KEY` | Obrigatória, com pelo menos 32 bytes. Gere com `python -c "import secrets; print(secrets.token_hex(32))"` |

## Como garantir que todo módulo emite no formato

No teste de cada módulo:

```python
from contratos.testing import assert_eventos_validos

def test_modulo_emite_no_contrato(tmp_path):
    rodar_modulo(saida=tmp_path / "eventos.jsonl")
    assert_eventos_validos(tmp_path / "eventos.jsonl", modalidade="video")
```

No CI ou na linha de comando:

```bash
python -m contratos.schema --check            # schema versionado bate com o modelo
python -m contratos.validar saida/*.jsonl     # sai com código 1 se houver evento inválido
pytest tests/contratos
```

## Consumidores fora do Python

O JSON Schema carrega as mesmas regras do catálogo via `allOf`/`if`/`then`, então dá para validar com Ajv (Node.js) ou qualquer validador 2020-12. O teste `test_schema_e_pydantic_rejeitam_os_mesmos_casos` garante que schema e pydantic concordam.

## Versionamento

- **Mudança compatível**, que gera versão _minor_ (1.1.0): novo `event_type`, novo campo opcional.
- **Mudança incompatível**, que gera versão _major_ (2.0.0) e novo arquivo `evento_achado.v2.schema.json`: remover ou renomear campo, mudar tipo, tornar um campo obrigatório.

Nos dois casos, `schema_version` e o `Literal` do modelo mudam juntos.
