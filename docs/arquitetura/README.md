# Arquitetura do fluxo multimodal

> **US04**: definir a arquitetura do fluxo multimodal e o contrato de eventos.
> Base para a seção "Descrição do fluxo multimodal" do relatório técnico (US21).

**Premissa:** trabalho acadêmico com orçamento próximo de zero. O pipeline roda em Python na máquina do grupo (o vídeo roda no Google Colab, que tem GPU gratuita). A AWS entra só onde o serviço gerenciado é o objetivo do desafio: transcrição, NLP e envio do alerta. Tudo é cobrado por uso, sem nenhum recurso ligado 24 horas.

- **Região:** `us-east-1`, onde todos os serviços usados estão disponíveis.
- **Serviços AWS:** Amazon S3, Amazon Transcribe, Amazon Translate, Amazon Comprehend, Amazon Comprehend Medical e Amazon SNS.

![Fluxo multimodal](fluxo_multimodal.png)

O GitHub também renderiza a fonte [`fluxo_multimodal.mmd`](fluxo_multimodal.mmd). A versão vetorial para o relatório está em [`fluxo_multimodal.svg`](fluxo_multimodal.svg). Para regenerar as imagens, rode `bash scripts/renderizar_diagrama.sh`.

## Visão geral

Um único comando executa o fluxo inteiro (US20):

```bash
python scripts/rodar_demo.py
```

O fluxo tem cinco passos:

1. **Ingestão.** O pipeline lê os arquivos de `dados/`, e o replay dos sinais vitais simula um stream em tempo real.
2. **Processamento.** Cada modalidade é processada no seu módulo. O áudio passa pelos serviços de IA da AWS.
3. **Detecção de anomalias.** Cada detector publica achados no formato [`EventoAchado`](contrato_eventos.md) em um barramento local.
4. **Fusão.** A fusão assina o barramento, calcula o risco por paciente e publica `risco_multimodal`.
5. **Alerta.** O motor de alertas assina o barramento e envia e-mail pelo Amazon SNS.

Os módulos não se chamam entre si, só publicam no contrato. Assim, cada integrante desenvolve e testa sua US isoladamente, e a fusão funciona com qualquer combinação de modalidades presentes, inclusive quando falta alguma (US18).

## Etapas e serviços AWS

Quando a etapa diz "nenhum", o código roda localmente, sem serviço AWS.

| Etapa | Onde roda | Serviço AWS | O que faz | US |
|---|---|---|---|---|
| **1 · Ingestão** | Local | nenhum | Arquivos em `dados/` (fora do Git, baixados por script). Replay do MIMIC-IV Demo que entrega as medições em ordem, em velocidade configurável | US03, US20 |
| **2 · Vídeo** | Google Colab (GPU) | nenhum | OpenCV, fps configurável, anonimização de rosto, OpenPose (keypoints) e YOLOv8 (objetos/ROIs). A saída é JSON em `saida/video/` | US05–US07 |
| **2 · Áudio** | Local | — | ffmpeg (WAV 16 kHz mono), biomarcadores com openSMILE/parselmouth, eventos respiratórios com YAMNet | US10, US12 |
| **2 · Transcrição** | AWS | **Amazon S3** `tc4-audio` + **Amazon Transcribe** | O áudio é enviado ao S3 (o Transcribe lê de lá) e transcrito em pt-BR com diarização. Uma regra de ciclo de vida apaga os arquivos após 7 dias | US11 |
| **2 · NLP clínico** | AWS | **Amazon Translate** → **Amazon Comprehend Medical** | O Comprehend Medical só aceita inglês, então as falas do paciente são traduzidas pt→en. Ele extrai sintomas, medicamentos e dosagens e marca negação ("nega dor no peito") | US13 |
| **2 · Sentimento** | AWS | **Amazon Comprehend** | Sentimento por sentença, direto em português | US13 |
| **2 · Sinais vitais** | Local | nenhum | Limpeza de artefatos (SpO2 = 0, FC > 300) e janela deslizante por paciente | US15 |
| **2 · Prescrições** | Local | nenhum | Ordenação por paciente e normalização de unidades | US16 |
| **3 · Detecção** | Local | nenhum | Regras e ML em Python: NEWS2 + Isolation Forest, regras ISMP/dose, postura/área crítica, imobilidade, classes vocais, termos críticos | US08, US13–US17 |
| **Barramento** | Local | nenhum | `BarramentoLocal`: valida cada evento, grava em `saida/eventos.jsonl` e entrega em ordem a quem assinou | US04 |
| **4 · Fusão** | Local | nenhum | *Late fusion* ponderada por paciente e janela; assina `modality ≠ fusao` | US18 |
| **5 · Alerta** | Local + AWS | **Amazon SNS** | Motor de alertas (prioridade IEC 60601-1-8, dedup, escalonamento) assina `severity ≥ media`, publica no tópico `alertas-equipe` (e-mail) e grava o log em `saida/alertas.jsonl` | US19 |

### Transversais

| Tema | Como |
|---|---|
| Credenciais AWS | Um usuário IAM do projeto com a política mínima de [`iam-politica-minima.json`](iam-politica-minima.json). As chaves ficam no perfil do AWS CLI ou no `.env`, nunca no Git |
| Controle de custo | **AWS Budgets** com alerta em US$ 10 (o próprio serviço de orçamento é gratuito). Cache local das respostas da AWS, descrito abaixo |
| Pseudonimização | `PSEUDONYM_KEY` no `.env` (HMAC-SHA256, ver [contrato](contrato_eventos.md)) |
| Logs e latência | Log estruturado por etapa. A latência de cada alerta é o instante do envio menos o `ingested_at` do evento de origem (US19) |

## Custo estimado

**Cenário:** 10 consultas de 5 minutos. Só as falas do paciente vão para o NLP clínico, cerca de 3.000 caracteres por consulta. Os preços são os de `us-east-1` sem nível gratuito:

| Serviço | Uso | Preço | Custo |
|---|---|---|---|
| Transcribe | 50 min | US$ 0,006/min | US$ 0,30 |
| Translate | 30 mil caracteres | US$ 15 por milhão | US$ 0,45 |
| Comprehend Medical | 300 unidades de 100 caracteres | US$ 0,01/unidade | US$ 3,00 |
| Comprehend (sentimento) | cerca de 600 sentenças, mínimo de 3 unidades cada | US$ 0,0001/unidade | US$ 0,18 |
| S3 + SNS | poucos MB e dezenas de e-mails | — | centavos |
| **Total por rodada completa** | | | **≈ US$ 4** |

O nível gratuito cobre esse volume para quem tem direito:

- Transcribe: 60 min/mês por 12 meses.
- Translate: 2 milhões de caracteres/mês por 12 meses.
- Comprehend Medical: 85 mil unidades no primeiro mês.
- Comprehend: 50 mil unidades/mês por API.

Contas novas recebem créditos em vez desse modelo, e as regras mudam com o tempo. Confiram o painel de faturamento da conta usada.

**Cache é obrigatório.** Toda chamada à AWS passa por um cliente que guarda a resposta em `saida/cache_aws/`, com o hash do arquivo ou do texto como chave. Rodar a demo de novo, gravar o vídeo ou ajustar limiares não repete chamadas já feitas. Assim, os ≈ US$ 4 são pagos uma vez, não a cada execução.

## Contrato de eventos

O contrato único de achado está descrito em [`contrato_eventos.md`](contrato_eventos.md). Em resumo:

- Implementação em `contratos/evento.py` (pydantic).
- JSON Schema exportado em `contratos/schema/evento_achado.v1.schema.json`.
- Exemplos por modalidade em `contratos/exemplos/`.
- Testes em `tests/contratos/`.

## Decisões de arquitetura

1. **Pipeline local, IA gerenciada na nuvem.** Filas, orquestradores, funções serverless e GPU na nuvem teriam custo e configuração sem ganho para a avaliação. A nuvem fica onde o desafio pede serviço gerenciado: transcrição, NLP e alerta.
2. **Barramento local no lugar de filas.** O `BarramentoLocal` dá o mesmo desacoplamento: os módulos só publicam no contrato e a fusão e os alertas assinam com filtros. Também grava tudo em JSONL, que serve de trilha de auditoria, fonte das métricas do relatório e replay da demo.
3. **Vídeo no Colab.** OpenPose e YOLOv8 precisam de GPU para rodar em tempo razoável. O Colab gratuito resolve, e o notebook grava os keypoints e as detecções em JSON para os detectores locais.
4. **Tradução antes do NLP clínico.** O Comprehend Medical e o Transcribe Medical só atendem inglês, e o Transcribe padrão não oferece modelo de linguagem customizado em pt-BR. Por isso a transcrição usa o Transcribe padrão (vocabulário personalizado se o WER passar de 20%), e o NLP clínico traduz pt→en. O texto original e o traduzido ficam na evidência para revisão.
5. **Evidência por referência.** Frames, trechos de áudio e transcrições ficam em `saida/`, e o evento guarda o caminho. O evento fica leve (no máximo 256 KB) e o JSONL continua legível.
6. **Pseudonimização na borda.** `patient_id` é um HMAC-SHA256: determinístico, para a fusão cruzar modalidades, e irreversível sem a chave (LGPD, art. 13 §4º).

## Evolução para produção (trabalhos futuros)

A arquitetura foi pensada para migrar sem reescrever os módulos, porque eles só dependem do contrato e de um `Emissor`:

| Hoje | Em produção |
|---|---|
| Replay local | Amazon Kinesis Data Streams |
| `BarramentoLocal` | Amazon EventBridge + SQS FIFO por paciente |
| Detectores e fusão em Python | AWS Lambda |
| Vídeo no Colab | AWS Batch com GPU |
| Log em JSONL | DynamoDB |

O limite de 256 KB do evento já é compatível com esses serviços. Vale citar esta seção no relatório técnico.

## Limitações conhecidas

- A tradução automática pode alterar termos clínicos. A US13 mede recall e precisão no texto em português anotado, para capturar esse erro de ponta a ponta.
- O "tempo real" é simulado pelo replay dos sinais vitais. Áudio e vídeo são processados por arquivo, depois da gravação.
- O MIMIC-IV desloca as datas para o futuro. O replay reescreve os timestamps para o instante da execução, e o valor original fica em `evidence.features`.
- Não é dispositivo médico validado clinicamente. Os achados são indicativos para avaliação da equipe.
