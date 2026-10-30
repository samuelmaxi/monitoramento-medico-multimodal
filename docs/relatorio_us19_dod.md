# Relatório US19 — rastreabilidade ao Definition of Done

**US19 · Motor de alertas automáticos para a equipe médica.** O enunciado pede
alertas gerados a partir das anomalias detectadas, enviados em tempo real. O DoD
abaixo é a proposta do grupo; cada linha aponta o código, o teste e o *status*
real.

Os caminhos são relativos à raiz do repositório.

## Requisito adicional: agnosticismo de modalidade

O serviço de notificação precisa funcionar mesmo com modalidades ainda em
desenvolvimento (US14, US15, US16, US17 e US18 não estão no `main`). Por isso:

- O motor conhece só o `EventoAchado` (contrato da US04). Não importa nenhum
  detector.
- Sem regra específica, a prioridade é a severidade do próprio achado. Por isso
  qualquer `event_type` do catálogo gera alerta sem mudar código ou YAML.
- O texto do alerta vem só dos campos do contrato. Texto específico de uma
  modalidade aparece apenas na `acao_sugerida` da regra, quando ela existe.
- Campos opcionais (`ingested_at`, `context`) não quebram: a latência cai para
  `detected_at` e o leito aparece como "não informado".
- Modalidade ausente não bloqueia nada: basta uma publicar para haver alertas.
- Para testar antes dos detectores, `alertas/simulador.py` gera achados
  sintéticos (`is_synthetic=True`) de qualquer tipo do catálogo.

## Fluxo

```text
Detectores (qualquer modalidade) ─► BarramentoLocal ─► MotorAlertas.receber   [thread de quem publicou]
                                                        │ regras.avaliar (pura)
                                                        │ dedup por janela (paciente + modalidade + tipo)
                                                        │ escalonamento por persistência (contagem ou tempo)
                                                        ▼
                              AlertStore: status=pending  (outbox, gravado antes do envio)
                                                        │ acordar(id), thread-safe
                                                        ▼
  Despachante (worker no lifespan do FastAPI): envia por canal ─► sent | failed | registered
        retentativa com espera exponencial · recuperação de pending/failed ao subir
  Timer (ALERTA_TIMER_S): escalonamento por tempo e retentativas
  alertas/armazenamento/jsonl.py ─► saida/alertas.jsonl (estado) + saida/alertas_eventos.jsonl (auditoria)
```

## Quadro DoD → código

### 1. Alertas automáticos a partir das anomalias, sem intervenção manual

**Status:** ✔ implementado.

- Assina o `BarramentoLocal` com `severity ≥ baixa` (`alertas/servico.py::ServicoAlertas.assinar`).
  `info` e `sem_achados` nunca viram alerta (`alertas/regras.py::avaliar`).
- Ligado ao app no `lifespan` (`app/main.py`), no mesmo barramento que os roteadores
  de texto e de vídeo usam (`app/barramento.py`).
- Testes: `tests/alertas/test_api_e_integracao.py::TestIntegracaoComBarramento`
  e `::test_lifespan_liga_o_motor_ao_barramento_que_os_roteadores_usam`.

### 2. Modalidades: vídeo (US09), áudio (US14), sinais vitais (US15), prescrições (US16), movimentação (US17) e fusão (US18)

**Status:** ✔ agnóstico; ✘ modalidades ainda não integradas ao `main`.

- O motor aceita qualquer modalidade do catálogo. Os testes percorrem os 27
  pares `(modalidade, tipo)` do catálogo e exigem um alerta enviado para cada um
  (`tests/alertas/test_motor.py::TestAgnosticismoDeModalidade`).
- Hoje só o texto (US13, `termo_critico`) e o vídeo (US07/US08) publicam no
  barramento. Os outros módulos entram quando o detector for mesclado, sem mudar o motor.
- O `risco_multimodal` da fusão (US18) precisa de `related_event_ids`. O motor
  aceita o achado como qualquer outro; o alerta ainda não referencia os eventos
  de origem, porque a US18 não existe.

### 3. Prioridade em 3 níveis, alinhada à IEC 60601-1-8

**Status:** ✔ implementado. Os valores de prioridade por regra são provisórios e precisam de validação clínica.

- Níveis `alta`, `media` e `baixa` (`contratos/catalogo.py::Severidade`).
- Política por prioridade e regras específicas em
  [`config/alertas_regras.yaml`](../config/alertas_regras.yaml). Exemplos:
  `sinais_vitais/news2_emergencia` → alta; `sinais_vitais/news2_resposta_urgente`
  → média; `movimentacao/queda` → alta.
- Assunto e cor do e-mail seguem a prioridade: `[ALTA]` em vermelho, `[MÉDIA]`
  em laranja, `[BAIXA]` em azul (`alertas/notificadores/formatacao.py`).

### 4. Conteúdo mínimo do alerta

**Status:** ✔ implementado e testado.

| Campo exigido | Onde está |
| --- | --- |
| Paciente pseudonimizado | `patient_id` (`pt_<24 hex>`); nunca nome |
| Leito | `bed_id`; "não informado" se ausente |
| Modalidade(s) | `modalities` |
| Anomalia | `event_type` |
| Valor e evidência | `summary`, `features`, `thresholds` |
| Horário | `event_at` |
| Severidade | `priority` |
| Ação sugerida | `suggested_action` (da regra ou da política) |

Teste: `tests/alertas/test_armazenamento_e_canais.py::test_texto_traz_os_campos_minimos_e_nao_traz_nome`.

### 5. Entrega em canal real (e-mail, Teams/Slack via webhook, Logic Apps ou Communication Services)

**Status:** ◐ parcial. O código e os testes estão prontos. A validação com um
servidor real depende de credenciais que não estão no repositório.

- **E-mail por SMTP** (`alertas/notificadores/email_smtp.py`): STARTTLS, login
  opcional, destinatários separados para alertas simulados. O envio roda em
  `asyncio.to_thread`. Testado com `smtplib` substituído. **Não testado contra
  Gmail ou Brevo real.**
- **Webhook** (`alertas/notificadores/webhook.py`): envia `{"text": ...}`, aceito
  por Teams e Slack. Testado com um servidor HTTP local, incluindo erro 500.
  **Não testado contra Teams ou Slack reais.**
- **Azure Communication Services Email:** ✘ não implementado. Entra como novo
  `Notifier` (fase 7 do plano), sem mudar o motor.

### 6. Registro em dashboard ou log

**Status:** ◐ parcial.

- Log auditável em `saida/alertas.jsonl` (estado) e `saida/alertas_eventos.jsonl`
  (auditoria, append-only).
- Endpoint `GET /alerts` com filtros por status, prioridade e paciente, e
  `GET /alerts/{id}` com a auditoria.
- Página de dashboard: ✘ não implementada (o plano a deixa por último).

### 7. Latência da ingestão até o envio: p95 ≤ 10 s

**Status:** ◐ medição pendente. O relatório existe; falta uma rodada com canal real.

- Cada alerta grava `ingested_at`, `detected_at`, `alert_created_at` e `sent_at`.
  Quando não há `ingested_at`, a medida usa `detected_at`, e o alerta registra
  isso em `latency_base`.
- `scripts/latencia_alertas.py` calcula p50, p95 e máximo e sai com código 2 se
  p95 > 10 s. Teste do cálculo: `tests/alertas/test_utilitarios.py`.
- Medição com canal real: pendente. Sem credenciais, o envio falha e não há
  latência útil para reportar.

### 8. Controle de fadiga: deduplicação, supressão por janela e escalonamento

**Status:** ✔ implementado e testado.

- Chave de deduplicação: `paciente | modalidade | event_type`
  (`alertas/dedup.py::chave_dedup`). Achado dentro da janela da prioridade vira
  ocorrência, sem novo envio (`SUPRIMIDO` na auditoria).
- Janela configurável por prioridade: 5 min (alta), 15 min (média), 60 min (baixa).
- Escalonamento por persistência, por contagem de ocorrências ou por tempo, sobe
  um nível e reenvia. Alta não escalona, por ser o topo. O timer faz o caso por tempo, sem
  achados novos.
- Testes: `tests/alertas/test_motor.py::TestCenarioDoD` e `::TestEscalonamento`.

### 9. Log auditável de todos os alertas

**Status:** ✔ implementado e testado.

- Tipos de auditoria: `created`, `suppressed`, `escalated`, `sent`, `failed`,
  `registered`, `acked`. Cada um traz quando, o quê e o detalhe.
- Reconhecido ou não: `status` (`ack`) e `acked_at`/`acked_by`.
- Teste: `tests/alertas/test_motor.py::TestCenarioDoD::test_duplicata_dentro_da_janela_e_suprimida`
  confere a sequência exata de auditoria.

### 10. Teste automatizado: cenário simulado gera exatamente o alerta esperado

**Status:** ✔ implementado.

- Cenário do DoD: SpO2 em 89% e NEWS2 = 8 (`alertas/simulador.py::cenario_news2_emergencia`).
  O teste exige exatamente um alerta de prioridade alta, enviado uma vez, e
  suprime a duplicata na janela.
- Persistência escala: `tests/alertas/test_motor.py::TestEscalonamento::test_persistencia_por_ocorrencias_escalona_baixa_para_media_e_envia`.

## Entrega por canal e retentativa

- Cada canal é independente. Se o webhook falha e o e-mail funciona, o e-mail
  não é reenviado; só o webhook é retentado (`pending_channels`).
- Backoff exponencial: 5 s, 10 s, 20 s, até `ALERTA_TENTATIVAS_MAX` (padrão 3).
  Depois disso o alerta fica `failed` e aparece no painel.
- Canal não configurado conta como falha e é retentado até esgotar.
- Recuperação: alertas `pending` e `failed` com tentativas restantes voltam para
  a fila quando o app sobe. O estado vem do JSONL, então um processo que caiu
  não perde alertas gravados.
- Teste: `tests/alertas/test_despachante.py`, incluindo a entrega de um achado
  publicado em outra thread até o worker do event loop.

## Reconhecimento

- Link no e-mail: `GET /alerts/{id}/ack?token=...`. API: `POST /alerts/{id}/ack?token=...`.
- O token é HMAC-SHA256 do identificador com `ALERTA_ACK_SECRET`. Sem o
  segredo, ninguém reconhece alerta alheio. Se o segredo não estiver definido, é
  gerado a cada execução e o link deixa de valer ao reiniciar.
- O link só funciona em `localhost` a menos que `ALERTA_PUBLIC_BASE_URL` aponte
  para um endereço público (túnel, por exemplo).

## Pendências

| Item | Motivo | Próximo passo |
| --- | --- | --- |
| Armazenamento em Cosmos DB (fase 5) | Exige conta, terraform e a dependência `azure-cosmos`; sem credenciais não há como testar | Implementar `CosmosStore` atrás do `AlertStore`, com `patient_id` como chave de partição |
| Azure Communication Services Email (fase 7) | Exige recurso ACS e domínio | Novo `Notifier`, sem mudar o motor |
| Página de dashboard | Plano a deixa por último | Streamlit ou página sobre `GET /alerts` |
| Medição de latência com canal real | Sem credenciais de SMTP ou webhook | Rodar `scripts/simular_alertas.py --todos` com `.env` configurado e depois `scripts/latencia_alertas.py` |
| Validação clínica das prioridades | As prioridades de `config/alertas_regras.yaml` são provisórias | Revisão pelo grupo clínico |

## Como reproduzir

```bash
uv run pytest tests/alertas -q                         # testes da US19
python scripts/simular_alertas.py                      # cenário do DoD
python scripts/simular_alertas.py --todos              # um achado por tipo do catálogo
python scripts/latencia_alertas.py saida/alertas.jsonl # p50, p95 e máximo; código 2 se p95 > 10 s
```

Configuração: [`.env.sample`](../.env.sample) (variáveis `ALERTA_*` e `SMTP_*`).
