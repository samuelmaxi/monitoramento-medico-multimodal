# Relatório técnico: monitoramento médico multimodal

**Tech Challenge, Fase 4 (US21).** Este relatório consolida a arquitetura, os modelos, os
resultados e as limitações do sistema. Os detalhes de cada história ficam nos relatórios de
rastreabilidade ao DoD em [`docs/`](.), citados em cada seção. Os caminhos são relativos à raiz
do repositório.

> **Aviso.** Este sistema **não é um dispositivo médico** e **não foi validado clinicamente**.
> É um trabalho acadêmico. Os achados indicam pontos para a equipe avaliar. Nenhum deles é
> diagnóstico nem substitui julgamento clínico. Os limiares e as prioridades de alerta são
> provisórios e precisam de revisão por profissionais de saúde antes de qualquer uso real.

## Sumário

1. [Escopo](#1-escopo)
2. [Fluxo multimodal](#2-fluxo-multimodal-us04)
3. [Modelos por tipo de dado](#3-modelos-por-tipo-de-dado)
4. [Resultados](#4-resultados)
5. [Exemplos de anomalias detectadas](#5-exemplos-de-anomalias-detectadas)
6. [Datasets, licenças e LGPD](#6-datasets-licenças-e-lgpd)
7. [Limitações e trabalhos futuros](#7-limitações-e-trabalhos-futuros)
8. [Referências](#8-referências)

## 1. Escopo

O sistema recebe quatro tipos de dado de pacientes internados ou em atendimento e publica
achados num contrato único, que alimenta um motor de alertas para a equipe:

| Tipo de dado | O que se detecta | US |
| --- | --- | --- |
| Vídeo (fisioterapia, leito, cirurgia) | postura fora da faixa, assimetria, queda, pessoa ou objeto em área crítica | US05 a US08 |
| Relatório de vídeo | relatório automático ao final de cada vídeo: desvios com início, fim, tipo, severidade, confiança e quadro de evidência, % do tempo em desvio e versões dos modelos | US09 |
| Áudio de consulta (texto) | transcrição, termos clínicos críticos, sentimento negativo | US11, US13 |
| Áudio de voz (acústica) | fadiga vocal, dificuldade respiratória, possível disartria, desvio da baseline vocal, tosse e respiração audível | US10, US12, US14 |
| Sinais vitais | NEWS2, desvio da baseline do paciente, tendência de piora | US15 |
| Prescrições | mudança abrupta de dose, duplicidade, suspensão abrupta, alta vigilância | US16 |
| Movimentação na internação | imobilidade > 2 h, saída do leito à noite, queda, agitação ou redução de mobilidade | US17 |
| Alertas | prioridade, deduplicação, escalonamento, entrega e auditoria | US19 |

## 2. Fluxo multimodal (US04)

![Fluxo multimodal](arquitetura/fluxo_multimodal.png)

Fonte editável: [`arquitetura/fluxo_multimodal.mmd`](arquitetura/fluxo_multimodal.mmd) (o GitHub
renderiza). Regenerar: `bash scripts/renderizar_diagrama.sh`. Decisões, custos e serviços em
[`arquitetura/README.md`](arquitetura/README.md).

O fluxo tem cinco passos:

1. **Ingestão.** Arquivos em `dados/` e `conteudos/`, fora do Git e baixados por script. Os sinais
   vitais do MIMIC-IV Demo passam por um replay que entrega as medições em ordem, numa velocidade
   configurável, para simular o tempo real.
2. **Processamento por modalidade.** Vídeo com OpenCV, OpenPose e YOLOv8. Áudio com `soundfile` e
   ffmpeg (WAV 16 kHz mono), **Azure AI Speech** (transcrição), **Azure AI Language** (Text
   Analytics for health e sentimento) e, só local, Praat/parselmouth e YAMNet (biomarcadores
   vocais e eventos respiratórios). Sinais vitais, prescrições e movimentação em Python local.
3. **Detecção.** Cada detector publica um `EventoAchado` v1
   ([contrato](arquitetura/contrato_eventos.md), JSON Schema em
   `contratos/schema/evento_achado.v1.schema.json`) no `BarramentoLocal`. O barramento valida cada
   evento e grava em `saida/eventos.jsonl`. No vídeo, a US09 fecha a análise de cada vídeo: publica os
   eventos e gera o relatório (JSON e HTML).
4. **Fusão** (US18, *late fusion* por paciente). Foi planejada e não está implementada (seção 7).
5. **Alerta.** O motor da US19 assina o barramento com `severity ≥ baixa`, aplica prioridade,
   deduplicação e escalonamento e envia por e-mail (SMTP) ou webhook (Teams/Slack). Grava log
   auditável em JSONL ou Azure Cosmos DB.

Os módulos não se chamam entre si; cada um só publica no contrato. Assim cada US é
desenvolvida e testada sozinha, e o motor de alertas funciona com qualquer combinação de
modalidades presentes. Os serviços Azure ficam só onde o desafio pede IA gerenciada (fala e NLP
clínico). Rodam em `brazilsouth`, então áudio e texto clínico não saem do país. O restante roda
local, com custo próximo de zero (≈ US$ 0,30 a 2 por rodada completa, com cache obrigatório das
respostas Azure em `saida/cache_azure/`).

Cada evento carrega `patient_id` pseudonimizado, `modality`, `event_type`, `timestamp`, `score`
(0 a 1), `severity` (`info`, `baixa`, `media`, `alta`) e `evidence`. A `evidence` tem `summary`
em português, `features` (valores observados), `thresholds` (limiares aplicados, com a versão) e
`artifacts` (quadro, trecho de áudio ou transcrição, por referência). Com isso, quem recebe o
alerta vê o valor que disparou a regra e a versão do limiar usado.

## 3. Modelos por tipo de dado

### 3.1 Vídeo

**Pré-processamento (US05).** OpenCV lê MP4/AVI e amostra a taxa configurada
(`VIDEO_TARGET_FPS=5`). Os rostos são anonimizados com o YuNet **antes** de qualquer gravação:
tarja sólida, margem de 15% e memória de 3 quadros.

**YOLOv8: objetos e áreas críticas (US07).** Detalhes no [relatório US07](relatorio_us07_dod.md).

| Item | Valor | Justificativa |
| --- | --- | --- |
| Modelo | `yolov8n.pt` (Ultralytics 8.4, pesos COCO) | Leve, roda em CPU e no Colab gratuito. Não houve fine-tuning: o repositório não tem rótulos clínicos |
| Classes | `person`, `bed`, `chair`, `couch`, `bottle` | Paciente ou cuidador e o mobiliário que define a borda e a lateral do leito |
| Confiança | `conf = 0,25` | Padrão Ultralytics, comparável à literatura |
| IoU (NMS e acerto) | `0,5` | Critério PASCAL VOC (mAP@0.5). O mesmo valor na inferência e na avaliação |
| Rastreamento | ByteTrack (`track_id`) | Entrada e saída pareadas por objeto e área |
| Áreas críticas | polígonos por câmera (`config/leito_uti_07.json`: `lateral_leito`, `borda_leito`) | Desenhados uma vez em resolução de referência e escalados |

**OpenPose: análise postural (US06, US08).** Detalhes no [relatório US08](relatorio_us08_dod.md).

| Item | Valor | Justificativa |
| --- | --- | --- |
| Modelo | OpenPose BODY_25 (Caffe, `cv2.dnn`), `video/pose/config.yaml` | 25 keypoints, inclusive pés e `MidHip`, que dão a escala do tronco |
| Entrada | altura 368 px, 10 fps | Padrão do OpenPose. Em CPU leva ~5× a duração do vídeo |
| Keypoint válido | confiança ≥ 0,3; esqueleto com ≥ 8 keypoints | Abaixo disso o ângulo fica ruidoso |
| Pós-processamento | pico 0,05; PAF 0,05; 80% de inliers | Valores do OpenPose original |

Regras de desvio da US08 (`config/us08_padroes.yaml`, versão **1.2.0**), com persistência mínima
de 1 s por padrão:

| Desvio | Regra |
| --- | --- |
| Postura e repetição (fisioterapia) | as 3 primeiras repetições, supervisionadas, viram a referência do paciente. As seguintes ficam a ±10° (abdução) ou ±15° joelho / ±20° tronco (agachamento) |
| Queda | centro do quadril desce ≥ 0,7 comprimento de tronco em ≤ 1 s e fica embaixo (janela 0,5 s) |
| Mudança brusca | tronco gira ≥ 60° em ≤ 0,5 s e se mantém |
| Presença indevida | classe proibida numa ROI da US07 por mais que o tolerado (borda do leito: 3 s) |

Os perfis de exercício comparam com a referência da própria sessão porque ângulos 2D mudam com a
pessoa e com a câmera. Um alvo absoluto de 130° na abdução separava as repetições corretas num
paciente e as invertia no seguinte.

**Relatório automático de desvios (US09).** Ao final da análise de cada vídeo,
`python -m video.relatorio.run` reaproveita a US08 (sem refazer a análise), publica os eventos no
barramento (os desvios de severidade **alta** chegam ao motor de alertas da US19) e grava o relatório
em **JSON e HTML**. O HTML é um arquivo único, sem script nem recurso externo, com os quadros de
evidência embutidos; o PDF sai pela impressão do navegador. Detalhes no
[relatório US09](relatorio_us09_dod.md); critérios de apresentação em `config/us09_relatorio.yaml`
(versão **1.0.0**).

| Item | Valor | Justificativa |
| --- | --- | --- |
| Identificação | só o `source_id` e o SHA-256 do arquivo: sem nome de arquivo, caminho, paciente nem leito. Uma varredura troca CPF, e-mail e caminhos por marcador | O DoD pede identificação sem dados pessoais (LGPD) |
| Confiança de cada desvio | regras de pose: média da confiança dos keypoints da medida nos quadros do trecho, com a % de quadros em que a medida estava completa. Área crítica: confiança da detecção do YOLOv8 (média da entrada e da saída). Abaixo de **0,5**, ou medida em menos de **50%** dos quadros, vira "baixa confiança" `[EXP]` | O `score` do evento mede a intensidade do desvio, não a certeza de que ele existe |
| % do tempo em desvio | une os trechos que se sobrepõem e corta na duração do vídeo | Somar contaria o mesmo instante duas vezes: no caso sintético da validação, a soma dava 111,7% do vídeo e a união, 83,3% |
| Modelos e limiares | OpenPose (modelo, pesos, versão do módulo), YOLOv8 (descrição e versão, lidas dos eventos da US07), versão da configuração e perfil inteiro de limiares da US08 | Um relatório antigo diz com que versões foi gerado |
| Vídeo sem desvios | "Sem desvios detectados". Com análise pouco confiável (pessoa pouco vista, sem pose), a mensagem avisa que a ausência de desvios **não é conclusiva** | Nunca um relatório vazio, e nunca "normal" sobre o que não foi visto |
| Alertas | todos os eventos do vídeo vão ao barramento e o motor decide pela severidade. Falha na publicação não impede o relatório: fica registrada nele e a CLI sai com 2 | O relatório sai mesmo com o barramento ou o motor fora do ar |

### 3.2 Áudio

**Azure AI Speech: transcrição (US11).** API de transcrição rápida (síncrona, o áudio vai na
requisição, sem armazenamento intermediário), com diarização de até 2 falantes e timestamps por
frase e palavra. A *phrase list* `app/frases_medicas.txt` (81 termos clínicos em pt-BR) só é usada
se o WER passar de 20%. Custom Speech é o passo seguinte e não foi implementado. Retry em 429/5xx
(3 tentativas, respeita `Retry-After`) e cache por SHA-256 do áudio.

Limiares do WER, pela orientação da Microsoft: ≤ 20% aceitável (limite do projeto), 5 a 10% bom,
≥ 30% não aceitável.

**Azure AI Language: termos críticos e sentimento (US13).** Só as falas do **paciente** são
enviadas (a pergunta do médico não gera achado). Detalhes no [README](../README.md#termos-críticos-e-sentimento-nas-transcrições-us13).

| Item | Valor | Justificativa |
| --- | --- | --- |
| Text Analytics for health | `language: "pt"` (o serviço não tem `pt-BR`) | Direto em português, sem tradução que troque termo clínico |
| Filtro de asserção | descarta `negative`, `negative_possible`, associação `other`, `hypothetical`/`conditional`. `past` rebaixa a severidade um nível | "nego dor no peito" e "meu pai teve infarto" não são achados do paciente |
| Lista de termos | `audio/text/critical_terms.yaml`, 15 termos versionados com sinônimos e UMLS | Lista fechada, revisável pela equipe |
| Sentimento | `pt-BR` (com `pt` o serviço assume pt-PT). Sentença negativa se score ≥ 0,7; consulta negativa se ≥ 50% das sentenças | Evento `sentimento_negativo` de severidade baixa, que dá contexto à equipe sem disparar alerta urgente |

**Ingestão, biomarcadores vocais e classificação (US10, US12, US14).** Rodam todos localmente, sem
Azure: a transcrição só entra como entrada *opcional*, para separar médico e paciente e contar
palavras. Desenho completo no [relatório técnico de áudio](relatorio_tecnico_audio.md) e nos
relatórios [US10](relatorio_us10_dod.md), [US12](relatorio_us12_dod.md) e
[US14](relatorio_us14_dod.md). Os limiares ficam em `audio/vocal/config.yaml`, cada um marcado
`[MDVP]` (normalidade do MDVP, citada na US12), `[PRAAT]` (padrão do Praat) ou `[EXP]`
(**experimental**, sem validação clínica).

| Etapa | Método | Parâmetros e limiares |
| --- | --- | --- |
| Conversão (US10) | `soundfile` (libsndfile), com ffmpeg como plano B; reamostragem polifásica (`scipy`); o WAV gravado é relido e validado | PCM 16 bits, 16 kHz, mono. Erro tipado por arquivo (vazio, corrompido, formato, curto, longo) sem parar o lote |
| Cópia para features (US10) | normalização de pico e remoção de silêncios longos, **só na cópia**; o original não muda (sha256) | pico alvo 0,95, ganho máximo 5×; silêncios ≥ 1 s (−30 dB relativo ao nível de fala) encurtados para 0,3 s |
| Biomarcadores (US12) | Praat via parselmouth 0.4.7, por segmento de fala do paciente: F0, jitter local, shimmer local, HNR, intensidade, pausas, taxa de fala | F0 75 a 500 Hz; jitter > 1,04 %, shimmer > 3,81 %, HNR < 20 dB `[MDVP]`; pausas ≥ 0,25 s a 25 dB do nível de fala `[EXP]` |
| Eventos respiratórios (US12) | YAMNet (AudioSet) em ONNX: tosse, respiração, sibilo, arfar, respiração ofegante | score mínimo 0,30 `[EXP]`; sem o modelo baixado a análise segue sem este critério |
| Baseline (US12) | z robusto (mediana/MAD) contra os áudios anteriores do mesmo paciente | ≥ 3 áudios; \|z\| ≥ 3,5 gera `desvio_baseline_vocal` |
| Classificação (US14) | regras por classe em YAML, com os critérios satisfeitos explicados no evento | fadiga vocal (2 critérios), dificuldade respiratória (2; **alta com 3**), possível disartria (3); nenhuma → `sem_achados` (normal) |

Pausas e taxa de fala são medidas no WAV convertido, **antes** da remoção de silêncio, que apagaria
justamente as pausas. Sem transcrição não há taxa de fala, e os critérios que dependem dela ficam
"não avaliáveis" e não contam. A única saída de severidade alta é a dificuldade respiratória com 3
critérios; de propósito, não há regra de áudio em `config/alertas_regras.yaml`, porque uma regra por
tipo substituiria essa prioridade. Os limiares MDVP valem para gravação de qualidade clínica: áudio
com perdas (Opus do WhatsApp, MP3) os ultrapassa, e o achado leva `audio_comprimido`. Toda saída é um
**indicativo para avaliação clínica, não um diagnóstico**.

### 3.3 Detecção de anomalias

**Sinais vitais: NEWS2 + ML não supervisionado (US15).** Detalhes no [relatório US15](relatorio_us15_dod.md)
e no [desenho](arquitetura/us15_sinais_vitais.md).

| Camada | Método | Parâmetros e limiares |
| --- | --- | --- |
| Limpeza | faixas fisiológicas e validade limitada | SpO2 < 50 e FC > 300 são descartadas; o último valor vale por tempo limitado e depois o NEWS2 sai com `news2_parcial` |
| Clínica | NEWS2 (RCP 2017, SpO2 escala 1) por parâmetro e total | total 5 a 6 ou 3 num parâmetro → `news2_resposta_urgente` (média); total ≥ 7 → `news2_emergencia` (alta) |
| ML: mudança súbita | z-score robusto (mediana/MAD) sobre 12 h do próprio paciente | \|z\| > **2,5** em **2** medições seguidas; a baseline congela durante o episódio |
| ML: tendência | inclinação da regressão linear em 6 h, na escala da baseline | limiar **2,5** |

Os limiares de ML saíram de uma busca em grade só nos pacientes de `subject_id` par. Entre os
pontos de F1 parecido, foi escolhido o de maior recall, porque perder deterioração custa mais que
um falso alarme. A arquitetura citava Isolation Forest; a implementação usa z-score robusto porque
não precisa de treino, aprende com cada paciente desde as primeiras horas, é explicável (o z vai na
evidência) e cumpriu a meta. Tudo roda local, sem o Azure AI Anomaly Detector, que foi
descontinuado.

**Prescrições: regras + lista ISMP (US16).** Detalhes no [relatório US16](relatorio_us16_dod.md);
limiares em `config/us16_prescricoes.yaml` v1.0.0. As unidades são normalizadas antes de qualquer
comparação (mcg/g → mg, L → mL).

| Regra | Dispara quando |
| --- | --- |
| `mudanca_abrupta_dose` | dose diária do mesmo produto e via muda ≥ **50%** em relação às **24 h** anteriores |
| `duplicidade_terapeutica` | mesmo princípio ativo (também dentro de combinações), via sistêmica, sobreposição ≥ **6 h** |
| `suspensao_abrupta` | classe que exige desmame (betabloqueador, corticoide, opioide etc.) em uso ≥ **72 h** some sem retorno em **24 h**, com o paciente internado |
| `medicamento_alta_vigilancia` | primeira ordem na internação de uma das **19 classes** da lista ISMP 2024 / ISMP Brasil 2019 |

As exceções vieram dos dados: PRN, ordens tituladas, esquema basal + correção de insulina, troca
de prescrição (`Inactive (Due to a change order)`), desmame prévio ≥ 25%, suspensão perto da alta.
Insulina, anticoagulante e opioide na introdução ficam em severidade baixa: são 55% das
introduções e, em média, gerariam 5,7 alertas por internação (fadiga de alarme).

**Movimentação: NPIAP/EPUAP + baseline (US17).** Detalhes no [relatório US17](relatorio_us17_dod.md);
limiares em `config/us17_movimentacao.yaml` v1.3.0. Duas fontes geram a mesma série: vídeo (pose, áreas da
US07, caixa do YOLOv8) e registro de enfermagem do MIMIC-IV (`Position`, `Turn`, `Activity`).

| Regra | Limiar |
| --- | --- |
| `imobilidade_prolongada` | > **120 min** sem mudança de posição (NPIAP/EPUAP), média; alta a partir de 240 min. Lacuna > 150 min não conta |
| `saida_do_leito` | alta à noite (22:00 a 06:00), média de dia |
| `queda` | sempre alta. Pelo vídeo: centro da caixa `person` desce ≥ **0,065** da altura do quadro em ≤ 1,5 s e fica embaixo 0,5 s |
| `agitacao_subita` / `reducao_mobilidade` | taxa de 6 h ≥ 2× ou ≤ 0,5× a baseline de 24 h do paciente (mínimo 12 h e 3 eventos de histórico) |

### 3.4 Motor de alertas (US19)

[Relatório US19](relatorio_us19_dod.md), `config/alertas_regras.yaml`. O motor conhece só o
contrato, então aceita qualquer `event_type` do catálogo sem mudar código.

- **Prioridade em 3 níveis** (alta, média, baixa), na linha da IEC 60601-1-8. `info` nunca alerta.
- **Fadiga de alarme:** deduplicação por `paciente | modalidade | event_type` em janela de 5 min
  (alta), 15 min (média) ou 60 min (baixa). Escalonamento por persistência (contagem ou tempo).
- **Entrega:** SMTP e webhook, com outbox (`pending` gravado antes do envio), retentativa
  exponencial (5, 10, 20 s) e recuperação ao reiniciar. Reconhecimento por link com token HMAC.
- **Registro:** `saida/alertas.jsonl` + trilha append-only, ou Cosmos DB. Painel em
  `GET /alerts/dashboard`.

## 4. Resultados

Todos os números foram medidos e podem ser reproduzidos pelos scripts citados. Nas avaliações com
dados rotulados, os limiares foram ajustados numa partição e o número reportado vem de outra. **A
exceção é a US14** (voz sintética com alterações injetadas, sem partição): os limiares são
anteriores à avaliação, mas partem dos mesmos autores dos casos, então o resultado é circular, como
a própria linha da tabela registra.

| US | Métrica | Meta | Obtido | Atingida? | Observação |
| --- | --- | --- | --- | :---: | --- |
| US07 | mAP@0.5 (COCO val2017, `yolov8n`) | sem meta (braço sem fine-tuning) | **0,528** | n/a | `saida/video/metricas_coco_val2017.json` |
| US07 | mAP@0.5:0.95 · Precision · Recall | n/a | **0,366** · **0,637** · **0,487** | n/a | Confere com o model card (0,373) |
| US07 | AP@0.5 das classes de interesse | n/a | person 0,503 · bed 0,425 · couch 0,431 · bottle 0,296 · chair 0,253 | n/a | |
| US07 | mAP@0.5 por classe médica após fine-tuning | ≥ 0,50 | não medido | ✘ | Sem rótulos clínicos no repositório. O gate (`scripts/dataset_us07.py treinar`) está pronto |
| US08 | Precision por evento (teste) | ≥ 0,70 | **0,71** | ✔ | 35 eventos, `scripts/avaliar_us08.py` |
| US08 | Recall por evento (teste) | ≥ 0,80 | **0,52** | ✘ | Queda só por pose tem recall 0,23 no FallVision. Abdução e agachamento ficam em 0,64 |
| US09 | Relatório automático por vídeo (JSON e HTML) com os 6 itens do DoD | 6 de 6 | **6 de 6** (52 testes; notebook de validação com 8 verificações, 8 atendidas) | ✔ | Pose, eventos e vídeo **sintéticos**: os JSONs de pose da US06 não são versionados. `docs/relatorio_us09_dod.md`, `notebooks/validacao_us09.ipynb` |
| US09 | Vídeo sem desvios gera "sem desvios detectados" | sim | 3 casos sintéticos (análise confiável, pouco confiável, pessoa nunca vista) e 3 arquivos reais de eventos da US07 | ✔ | Os 3 reais saem "pouco confiáveis": sem pose, a postura não foi avaliada |
| US09 | Desvio de severidade alta chega ao motor de alertas (US19) | sim | queda sintética vira alerta de prioridade alta (motor real, canais falsos) | ✔ | Envio real por e-mail ou webhook não foi testado (sem canal configurado) |
| US11 | WER (Azure Speech) | ≤ 20% | não medido | ✘ | Rodada de 06/11/2026: os 10 áudios do Eka voltaram HTTP 401. A assinatura Azure do grupo está desativada (seção abaixo). Reproduzir: `python scripts/avaliar_wer.py --baixar 10` |
| US13 | Precision / Recall / F1 de termos críticos | sem meta fixa | não medido | n/a | Mesma assinatura desativada; `AZURE_LANGUAGE_KEY` também está vazia no `.env`. Reproduzir: `python scripts/eval_us13.py` |
| US10 | Conversão para WAV PCM 16 kHz mono; arquivo ruim não para o lote; original preservado | todos | atendido: cada conversão é relida e validada; erro por arquivo em log e em `metadados.csv`; sha256 do original inalterado | ✔ | `tests/audio/test_ingestao_*.py` |
| US12 | Biomarcadores por segmento do paciente (F0, jitter, shimmer, HNR, intensidade, pausas, taxa de fala) e eventos respiratórios (YAMNet) | todos | implementados. Em voz sintética: F0 148,8 Hz contra 148,35 Hz esperados (0,3 %); jitter injetado de 2 % medido em 1,96 % | ✔ | YAMNet verificado só em silêncio e em tons, **não** em tosse ou respiração reais |
| US14 | Recall por classe (casos injetados, 60 por classe) | ≥ 0,80 | fadiga **1,00** · dificuldade respiratória **1,00** · disartria **1,00** (IC95 0,94 a 1,00) | ✔ | **Circular**: limiares e casos injetados são dos mesmos autores; mede a lógica das regras, não a validade clínica. FP em voz normal sintética 0/60. `saida/audio/avaliacao_us14.json` |
| US14 | Exemplo de alteração vocal detectada | ≥ 1 | 1 caso **sintético** (seção 5.6); os 3 áudios reais (OGG/Opus) não dispararam classe | ✔ (sintético) | Os 3 reais ultrapassaram jitter, shimmer e HNR juntos: artefato de codec, não alteração |
| US15 | Recall (teste, 50 pacientes, 2.924 slots) | ≥ 0,80 | **0,838** | ✔ | VP 1.264 · FN 244 · FP 574 · VN 842 |
| US15 | Precision (teste) | n/a | **0,688** | n/a | Piso: parte dos FP é piora real sem rótulo |
| US15 | F1 (teste) | ≥ 0,70 | **0,756** | ✔ | `saida/sinais/relatorio_us15.json` |
| US15 | Latência por medição | tempo real | ~0,1 ms | ✔ | Janela deslizante em `deque` |
| US16 | Recall (casos injetados, modo realista) | ≥ 0,80 | **0,974** (IC95 0,967 a 0,980) | ✔ | Cada tipo ≥ 0,94. FP em controles 1,5%. `saida/prescricoes/avaliacao_us16.json` |
| US16 | Recall (modo controlado) | ≥ 0,80 | **1,000** | ✔ | FP 0,4% |
| US17 | Recall de queda em vídeo real (teste) | ≥ 0,80 | **0,875** (14/16, IC95 0,64 a 0,97) | ✔ (pontual) | O limite inferior do IC fica abaixo de 0,80. Alarme falso 1/5 |
| US17 | Recall nos casos injetados no MIMIC | n/a | queda 1,00 · saída noturna 1,00 · imobilidade 1,00 · agitação 0,988 · redução 0,822 | n/a | Verifica a lógica, não a percepção |
| US19 | Latência ingestão → envio, p95, canal local | ≤ 10 s | **0,004 s** (p50 0,002 · máx 0,026) | ✔ | 100 achados, webhook HTTP local: mede só o overhead do motor |
| US19 | Latência ingestão → envio, p95, **e-mail SMTP real** | ≤ 10 s | **54,97 s** (p50 34,99 · máx 54,97) | ✘ | 10 achados simultâneos, 9 enviados. `saida/latencia_us19_smtp.json` |

### Metas não atingidas e por quê

- **US07, fine-tuning em classes médicas.** Não há dataset clínico anotado com licença
  compatível. O pipeline de treino e o gate por classe existem, mas sem rótulos não há número para
  declarar.
- **US08, recall de 0,52.** O ponto fraco é a queda vista só pela pose: nos clipes curtos do
  FallVision a pessoa aparece em menos de 50% dos quadros, ou o clipe acaba logo após o impacto.
  Na abdução, uma referência contaminada (PM_039 começa com repetição incorreta) derruba o recall.
  A US17 mostrou a correção: com a descida da caixa do YOLOv8, o recall de queda vai a 0,875.
- **US09, sem desvio de pose real.** A confiança, o % do tempo e o HTML foram verificados com pose e
  eventos **sintéticos** e com eventos reais da US07 sem pose, porque os JSONs de pose da US06 não são
  versionados e os vídeos do dataset não estão no repositório. Falta gerar o relatório sobre um desvio
  de pose real (por exemplo, a queda de `B_N_03` da US08). Os limiares de confiança (0,5 e 50% de
  cobertura) são experimentais.
- **US11 e US13, não medidos.** Na rodada de 06/11/2026 com o `.env` do grupo, o Speech
  respondeu HTTP 401 aos 10 áudios, em `eastus` e em `brazilsouth`. O Cosmos DB, na mesma
  assinatura, respondeu `Forbidden: Subscription owning the database account is disabled`. A
  assinatura Azure está desativada (o crédito do Azure for Students para os recursos quando
  acaba). A US13 também não tem `AZURE_LANGUAGE_KEY`. Os scripts estão prontos e testados com a
  API simulada (`pytest audio/tests`). Reativada a assinatura, basta rodá-los e copiar os números.
  Não há dataset de referência em pt-BR, por isso o WER usa o Eka Medical ASR em inglês indiano.
  Esse desvio do DoD fica registrado.
- **US19, latência com e-mail real (p95 55 s).** O motor leva 4 ms. O tempo vem da entrega: o
  `Despachante` (`alertas/despachante.py`) envia um alerta por vez, e o `NotificadorEmail` abre
  uma conexão SMTP nova a cada e-mail (STARTTLS + login, ~5,5 s no provedor). Com 10 alertas
  simultâneos, o décimo espera os nove anteriores, e a latência cresce em linha reta (p50 35 s).
  Um alerta isolado sai em ~5 s, dentro da meta, mas uma rajada não. Um dos 10 e-mails falhou na
  primeira tentativa e ficou para retentativa. Correção proposta: reutilizar a conexão SMTP e
  enviar em paralelo por canal (seção 7).
- **US12 e US14, sem validação com voz real.** O recall de 1,00 vem de voz sintética e é circular.
  Faltam gravações rotuladas por fonoaudiólogo, que o projeto não tem, para validar os limiares
  `[EXP]` e para medir o YAMNet em tosse e respiração reais. Nenhum dos 3 áudios reais disparou uma
  classe, então o exemplo documentado é sintético e **falta uma gravação real alterada** para o
  vídeo. O caminho com transcrição real (`--transcrever`) também não foi exercitado, pela mesma
  assinatura Azure desativada. Detalhes no [relatório de áudio](relatorio_tecnico_audio.md#4-resultados).

## 5. Exemplos de anomalias detectadas

Um exemplo por tipo, com o evento resumido e a evidência. IDs de paciente são pseudônimos HMAC.

### 5.1 Vídeo: queda (FallVision `B_N_03`, perfil `internacao_leito`)

O quadril desceu 1,1 comprimento de tronco em 0,5 s e ficou embaixo. Quadro 216 (1,80 s),
anonimizado:

![Queda em B_N_03](evidencias/us08/fallvision_B_N_03_queda_1.80s.jpg)

```json
{"modality": "video", "event_type": "mudanca_brusca_ou_queda", "severity": "alta", "score": 0.7087,
 "evidence": {"summary": "Possível queda aos 0,9 s: o quadril desceu 1,1 comprimentos de tronco em 0,5 s e ficou embaixo por 1,1 s.",
   "features": {"regra": "queda", "valor": 1.117, "frame_index": 216},
   "thresholds": {"versao_limiares": "1.1.0", "limiar": 0.7, "janela_minima_s": 0.5}}}
```

Outro desvio real (repetição incorreta de abdução, REHAB24-6 `PM_001`, ombro em 122° contra 135°
da referência): [`evidencias/us08/rehab24_PM_001_repeticao8_81.80s.jpg`](evidencias/us08/rehab24_PM_001_repeticao8_81.80s.jpg).
Entrada e saída de área crítica (US07, `B_D_0016`, 10 eventos pareados):
`saida/video/eventos_us07.jsonl` e `saida/video/rois_leito_uti_07.jpg`.

### 5.2 Áudio: termo crítico afirmado e negação descartada (US13)

Consulta simulada `data/text_samples/consulta-01.json`, anotada em
`data/text_samples/annotations.csv`. O paciente diz "estou com uma dor no peito muito forte". O
médico pergunta "você sente falta de ar?" e o paciente nega febre. Saída esperada: um
`termo_critico` para dor torácica e nenhum achado para a pergunta do médico nem para a febre
negada.

```json
{"modality": "texto", "event_type": "termo_critico", "severity": "alta",
 "evidence": {"summary": "Paciente relata dor torácica: “dor no peito” (afirmado, sem negação)",
   "features": {"termo": "dor torácica", "nome_normalizado": "Chest Pain", "certeza": "positive",
                "associacao": "subject", "falante": "paciente", "offset_ms": 6512},
   "artifacts": [{"kind": "transcricao", "uri": "data/text_samples/consulta-01.json", "start_ms": 3615, "end_ms": 11675}]}}
```

> Este evento mostra o formato com valores ilustrativos (o score do serviço não está nele). A
> rodada real com o Azure AI Language grava o exemplo medido em `outputs/text_analysis/eval.md`,
> que deve substituir este bloco.

### 5.3 Sinais vitais: NEWS2 de emergência (MIMIC-IV Demo, replay)

Gerado por `python scripts/rodar_us15.py --velocidade 0 --pacientes 3` (o replay reescreve o
horário para o instante da execução):

```json
{"modality": "sinais_vitais", "event_type": "news2_emergencia", "severity": "alta",
 "patient_id": "pt_b00c3ff10002574317845f45",
 "evidence": {"summary": "NEWS2 = 8 (fr 24, pas 84, fc 91, o2_suplementar 1): emergência",
   "features": {"fr_irpm": 24, "pas_mmhg": 84, "fc_bpm": 91, "spo2_pct": 98, "temp_c": 37.06, "o2_suplementar": 1,
                "news2_fr": 2, "news2_pas": 3, "news2_fc": 1, "news2_o2_suplementar": 2, "news2_total": 8,
                "news2_parcial": true, "news2_ausentes": "alerta"},
   "thresholds": {"news2_urgente_total": 5, "news2_emergencia_total": 7, "news2_parametro_unico": 3}}}
```

Na mesma rodada, a camada de ML marcou `desvio_baseline` noutro paciente: "pas 89 desvia da
baseline do paciente (mediana 105, z = -2.7)", limiar |z| 2,5 em 2 medições.

### 5.4 Prescrições: mudança abrupta de anticoagulante (MIMIC-IV Demo)

`pt_472b69fdffa5894af2fbb0c0`, ordens 41490219 → 58986734, `mudanca_abrupta_dose`, média,
score 1,0:

> Dose de Enoxaparin Sodium (SC) subiu 400% (40 → 200 mg/dia), 1 h depois do fim da anterior.
> Antes: 40 mg QPM (40 mg/dia); depois: 100 mg Q12H (200 mg/dia). Limiar: ±50% em 24 h.

Profilaxia trocada por dose plena. Pode ser conduta correta (trombose diagnosticada), mas
anticoagulante cinco vezes maior de uma hora para outra pede dupla checagem de peso e função
renal. Outros três casos reais, um de cada tipo, no [relatório US16](relatorio_us16_dod.md#anomalias-reais-do-mimic-iv-demo-item-7).

### 5.5 Movimentação: imobilidade prolongada (MIMIC-IV Demo)

Em `saida/movimentacao/mimic_eventos.jsonl` (861 eventos válidos de 139 estadias):

```json
{"modality": "movimentacao", "event_type": "imobilidade_prolongada", "severity": "alta", "score": 1.0,
 "patient_id": "pt_c2e6536fb12486a755b270c0",
 "evidence": {"summary": "4 h 00 min sem mudança de posição no leito",
   "features": {"minutos_sem_mudanca": 240.0, "reposicionamentos_por_hora": 0.426},
   "thresholds": {"limiar_min": 120.0, "lacuna_max_min": 150.0, "severidade_acima_de_min": "240:alta", "versao_limiares": "1.3.0"}}}
```

Queda em vídeo real pela descida da caixa: `python -m movimentacao.run video --video conteudos/videos/B_D_0002.mp4`
gera um evento `queda` de severidade alta.

### 5.6 Áudio vocal: dificuldade respiratória (caso sintético)

Voz sintética de dificuldade respiratória (3 frases curtas, pausas longas, voz soprosa), pela CLI
`python -m audio.vocal.run`, com o Praat e o YAMNet reais. Nenhum evento respiratório passou do score
mínimo; o achado veio de **3 de 4 critérios** (pausas, fala lenta e HNR baixo):

```json
{"modality": "audio", "event_type": "dificuldade_respiratoria", "severity": "alta", "score": 0.75,
 "model_version": "audio-achados@0.1.0",
 "evidence": {"summary": "Indicativo para avaliação clínica, não é diagnóstico: padrão compatível com dificuldade respiratória (3 de 4 critérios): pausas ocupam 50% da fala (limite 40%); taxa de fala 0.92 palavras/s (mínimo 1.8 palavras/s); HNR 18.17 dB (mínimo 20 dB)",
   "features": {"regras_acionadas": "pausas_altas, fala_lenta, hnr_baixo", "proporcao_pausas": 0.499,
                "taxa_fala_pps": 0.919, "hnr_db": 18.17, "jitter_local_pct": 0.519,
                "shimmer_local_pct": 2.635, "eventos_respiratorios": 0},
   "thresholds": {"versao_limiares": "1.0.0", "minimo_criterios": 2, "criterios_forte": 3,
                  "proporcao_pausas_max": 0.4, "taxa_fala_pps_min": 1.8, "hnr_db_min": 20.0}}}
```

O motor de alertas gerou um alerta de prioridade `alta` (origem `padrao:alta`) com a ação sugerida
"Avaliar o paciente agora, conforme o protocolo do setor." O envio ficou `failed` só porque nenhum
canal (SMTP ou webhook) estava configurado no ambiente; o alerta está registrado no painel. Na mesma
rodada dispararam também `fadiga_vocal` e `possivel_disartria` (média): as classes se sobrepõem. Os
três áudios reais de voz (OGG/Opus) saíram `normal` com `audio_comprimido`; ver o
[relatório de áudio](relatorio_tecnico_audio.md#5-exemplos-de-achados).

### 5.7 Vídeo: relatório automático de uma queda e de uma presença na borda do leito (US09)

> **Exemplo sintético.** A pose e os eventos de área foram gerados no notebook de validação
> ([`notebooks/validacao_us09.ipynb`](../notebooks/validacao_us09.ipynb)): uma pessoa em pé até 4 s,
> que cai e fica no chão até os 12 s, e uma detecção na borda do leito de 2,0 s a 6,5 s. Não é um
> desvio de pose real.

![Relatório HTML da US09](evidencias/us09/relatorio_com_desvios.png)

Trecho do JSON (`<fonte>_relatorio_desvios.json`):

```json
{"video": {"id": "sintetico", "tipo_procedimento": "leito", "perfil": "internacao_leito", "duracao_s": 12.0},
 "resumo": {"status": "desvios_detectados", "total_desvios": 2,
            "por_severidade": {"alta": 1, "media": 1, "baixa": 0}, "pct_tempo_em_desvio": 83.33},
 "desvios": [
   {"tipo": "mudanca_brusca_ou_queda", "severidade": "alta", "inicio_s": 3.1, "fim_s": 12.0,
    "confianca": 0.9, "evidencia": {"frame_index": 43}},
   {"tipo": "presenca_indevida_area_critica", "severidade": "media", "inicio_s": 2.0, "fim_s": 6.5,
    "confianca": 0.71, "evidencia": {"frame_index": 20}}],
 "modelos": {"openpose": {"modelo": "BODY_25", "versao_modulo_pose": "0.1.0"},
             "yolov8": {"descricao": "yolov8n@8.4.173"}},
 "limiares": {"versao": "1.2.0"},
 "alertas": {"desvios_severidade_alta": 1, "falhas": []}}
```

Os dois desvios acontecem ao mesmo tempo, então o % do tempo em desvio une os trechos (83,3%) em vez
de somá-los (111,7%). O desvio de queda vai de 3,1 s até o fim do vídeo porque a US08 o mantém aberto
enquanto a pessoa permanece no chão: por isso a queda sozinha responde por 74,2% do vídeo. A confiança
de 0,90 vem dos keypoints do tronco; a de 0,71 é a média das detecções do YOLOv8 na entrada (0,80) e
na saída (0,62) da área.

## 6. Datasets, licenças e LGPD

### Datasets

Nenhum dado bruto é versionado. Tudo é baixado por script para `dados/`, `data/` ou `conteudos/`,
que estão no `.gitignore`. Ficam no Git só os resultados agregados, as anotações próprias e as
evidências anonimizadas. Áudio de voz nunca é versionado: o `.gitignore` cobre as extensões de
áudio e um teste confere `git ls-files`.

| Dataset | Uso | Licença |
| --- | --- | --- |
| [MIMIC-IV Clinical Database Demo 2.2](https://physionet.org/content/mimic-iv-demo/2.2/) (PhysioNet) | sinais vitais (US15), prescrições (US16), enfermagem (US17) | ODbL v1.0 |
| REHAB24-6 (Černek et al., 2024) | fisioterapia, pose e desvios (US06, US08) | CC BY-NC 4.0, uso não comercial com atribuição |
| FallVision (clipes de leito) | queda (US08, US17) | conferir no repositório de origem antes de redistribuir. Os clipes não são versionados |
| [COCO val2017](https://cocodataset.org/) | métricas do YOLOv8 (US07) | anotações CC BY 4.0; imagens sob os termos do Flickr |
| [Eka Medical ASR Evaluation](https://huggingface.co/datasets/ekacare/eka-medical-asr-evaluation-dataset) | WER (US11) | MIT |
| [PriMock57](https://github.com/babylonhealth/primock57) (Babylon Health, 2022) | transcrições de consultas simuladas (US13) | CC BY 4.0 |
| [MedPT](https://huggingface.co/datasets/AKCIT/MedPT) (AKCIT, 2026) | perguntas reais de pacientes em pt-BR (US13) | card sem licença: o texto fica fora do Git, só IDs e rótulos são versionados |
| `data/text_samples/` (15 consultas) | NLP em pt-BR (US13) | criadas pelo grupo, simuladas |
| Pesos `yolov8n.pt` e OpenPose BODY_25 | inferência | AGPL-3.0 (Ultralytics); OpenPose: licença acadêmica não comercial da CMU |
| Voz sintética (`audio/vocal/injecao.py`) | testes e avaliação da US14 | gerada pelo grupo, em memória; nada a redistribuir |
| 3 áudios de voz (OGG/Opus), só na máquina local | rodada com áudio real (US12) | não versionados; o consentimento do titular precisa ser confirmado |
| [YAMNet](https://github.com/tensorflow/models/tree/master/research/audioset/yamnet) em ONNX (espelho `andrelgomes/yamnet-onnx`) e mapa de classes do AudioSet | eventos respiratórios (US12) | Apache-2.0, conforme o script de download, que fixa o SHA-256. O ONNX é uma conversão de terceiros |
| Praat, via `praat-parselmouth` 0.4.7 | biomarcadores vocais (US12) | GPL-3.0 ou posterior: avaliar antes de qualquer redistribuição |

### Tratamento LGPD

Dados de saúde são dados pessoais sensíveis (LGPD, art. 5º, II). As medidas:

| Medida | Como |
| --- | --- |
| Pseudonimização na borda (art. 13 §4º) | `patient_id` e `encounter_id` viram `pt_<24 hex>` por HMAC-SHA256 com `PSEUDONYM_KEY` (≥ 32 bytes, só no `.env`), em `contratos/pseudonimizacao.py`. É determinístico para a fusão cruzar modalidades e irreversível sem a chave. Nenhum `subject_id` cru aparece no evento (testado) |
| Anonimização de imagem | rostos tarjados antes de qualquer gravação (YuNet). Nos quadros de evidência, a cabeça é tarjada pelos keypoints do rosto |
| Relatório de vídeo (US09) | identifica o vídeo só pelo `source_id` e pelo SHA-256: nunca o nome do arquivo, o caminho, o paciente nem o leito (o pseudônimo fica nos eventos e nos alertas). Uma varredura troca CPF, e-mail e caminhos por marcador, e os quadros de evidência saem com a cabeça tarjada. Ressalva: um `source_id` tirado do nome do arquivo pode conter um nome de pessoa, que a varredura não reconhece; use `--source-id` neutro |
| Voz (US10, US12, US14) | `patient_id` pseudonimizado antes de qualquer cálculo; a baseline vocal é indexada só pelo pseudônimo e a transcrição é gravada com ele. Praat, YAMNet e regras rodam localmente; só a transcrição opcional envia áudio ao Azure. Metadados com `origem` e `licenca` e o nome do arquivo, nunca o caminho absoluto. `audio_id` validado, porque vira nome de pasta |
| Minimização | só as falas do paciente vão ao NLP. Evidência por referência: o evento guarda o caminho, não o conteúdo. Alertas não trazem nome |
| Localização dos dados (art. 33) | Azure Speech e Language em `brazilsouth`, então áudio e texto não saem do país. A transcrição rápida não guarda o áudio em armazenamento na nuvem |
| Segredos | sem chave no Git. A arquitetura alvo usa `DefaultAzureCredential` com função RBAC mínima ([`azure-funcao-minima.json`](arquitetura/azure-funcao-minima.json)); em produção, a chave HMAC iria para o Key Vault |
| Base dos dados | só dados públicos e desidentificados na origem (MIMIC) ou simulados. MIMIC desloca as datas para o futuro |
| Auditoria | barramento e alertas em JSONL append-only, com reconhecimento por token HMAC |

Os três áudios de voz da rodada com áudio real ficam só na máquina local e não são versionados; o
consentimento dos titulares precisa ser confirmado. Fora isso, não houve coleta de dados de pessoas
reais pelo grupo. Um uso clínico real exigiria base legal (art. 11), relatório de impacto (RIPD),
contrato de operador com a Microsoft e política de retenção, e o grupo não fez nenhum deles.

## 7. Limitações e trabalhos futuros

**Não é dispositivo médico validado clinicamente.** Nenhum limiar, prioridade ou regra foi
revisado por equipe clínica nem avaliado prospectivamente. Uso assistencial exigiria validação
clínica e enquadramento regulatório (ANVISA, RDC 657/2022, software como dispositivo médico).

**Limitações**

- **Avaliação com rótulo sintético ou pequeno.** US15, US16 e US17 (MIMIC) medem anomalias
  injetadas, não eventos clínicos reais. US17 tem 16 quedas no teste, com rótulos que ainda pedem
  conferência humana.
- **Vídeo.** Ângulos 2D dependem da câmera. A referência de fisioterapia só vale se as 3 primeiras
  repetições forem corretas. Câmera vista de cima não mostra queda como descida. A US07 não separa
  paciente de profissional. Sem fine-tuning, não há classes cirúrgicas. Rostos pequenos, inclinados
  ou cobertos podem escapar da anonimização.
- **Áudio.** WER e US13 sem rodada real nesta entrega. Não há dataset de referência em pt-BR.
  O Text Analytics for health é mais maduro em inglês. A API GA não devolve temporalidade, então o
  passado é inferido por marcadores locais.
- **Áudio vocal (US10, US12, US14).** Os limiares `[EXP]` não têm validação clínica, e a avaliação
  em voz sintética é circular. Os limiares MDVP são de gravação de qualidade clínica: áudio com perdas
  (Opus do WhatsApp) ultrapassa os três de uma vez, e o sistema sinaliza `audio_comprimido`, mas não
  corrige. O módulo não faz supressão de ruído, e ruído de fundo reduz o HNR. O YAMNet não foi medido
  em tosse ou respiração reais. Sem transcrição não há taxa de fala. Ver as
  [limitações do relatório de áudio](relatorio_tecnico_audio.md#7-limitações-e-trabalhos-futuros).
- **Relatório de vídeo (US09).** A confiança por desvio e seus limiares (0,5 e 50% de cobertura) são
  experimentais, sem calibração com vídeos rotulados. O desvio de queda dura até a pessoa deixar de estar
  no chão, então o "% do tempo em desvio" de uma queda mede a permanência, não a duração do evento. Não há
  rota na API (o vídeo enviado é apagado ao fim do job, e sem ele não há quadro de evidência) nem PDF
  gerado direto (imprime-se o HTML). Herda as limitações de detecção da US08.
- **Prescrições.** O MIMIC registra ordens, não administrações. Sem peso nem indicação, toda mudança
  grande de dose é anomalia, mesmo quando é a conduta certa. O princípio ativo vem de vocabulário
  próprio, não do RxNorm.
- **Tempo real.** Só os sinais vitais são streaming (replay). Vídeo e áudio são processados por
  arquivo.
- **Alertas.** Com SMTP real, a entrega em série estoura a meta de 10 s em rajadas. Teams e
  Slack reais não foram testados (sem `ALERTA_WEBHOOK_URL`). O armazenamento no Cosmos DB não foi
  validado contra a conta real, por causa da assinatura desativada. Azure Communication Services
  Email não foi implementado.
- **Dependência da assinatura Azure.** Transcrição, NLP clínico e Cosmos param juntos quando a
  assinatura é desativada. Vídeo, sinais vitais, prescrições, movimentação e alertas por SMTP
  continuam funcionando, porque rodam local.

**Trabalhos futuros**

1. **Fusão multimodal (US18):** *late fusion* ponderada por paciente e janela, publicando
   `risco_multimodal` com `related_event_ids`.
2. **Calibrar e validar os biomarcadores vocais (US12, US14):** limiares `[EXP]` com gravações
   rotuladas por fonoaudiólogo, YAMNet medido em tosse e respiração reais, estimativa de SNR e
   supressão de ruído, e openSMILE (eGeMAPS) para comparar com o Praat.
3. **Rodar WER e US13 com os recursos Azure** e, se o WER passar de 20%, Custom Speech em pt-BR
   (treino em `eastus`, cópia para `brazilsouth`).
4. **Fine-tuning do YOLOv8** em classes médicas (instrumentos, monitores) com o gate por classe
   ≥ 0,50 já implementado.
5. **Calibrar postura pela pose** em vídeo real de leito e confirmar pelo fisioterapeuta a série
   de referência.
6. **Produção na Azure:** Event Hubs/IoT Hub na ingestão, Service Bus no lugar do barramento
   (sessões por `patient_id`), Functions ou Container Apps, Key Vault, Application Insights e
   Health Data Services (FHIR). Ver [arquitetura](arquitetura/README.md#evolução-para-produção-trabalhos-futuros).
7. **Entrega de alertas em paralelo:** conexão SMTP reutilizada (ou ACS Email, que é HTTP) e
   envio concorrente por canal no `Despachante`, para p95 ≤ 10 s também em rajadas.
8. **Validação clínica** dos limiares e prioridades com a equipe, e estudo retrospectivo com
   eventos reais rotulados.
9. **Relatório de vídeo (US09):** gerar o relatório sobre desvios de pose reais (rodando a US06 de
   novo nos vídeos do dataset), calibrar os limiares de confiança com vídeos rotulados, oferecer rota
   na API (guardando o vídeo ou o quadro de evidência) e PDF direto.

## 8. Referências

1. Royal College of Physicians. *National Early Warning Score (NEWS) 2: Standardising the
   assessment of acute-illness severity in the NHS.* London: RCP, 2017.
2. Microsoft. *Test accuracy of a Custom Speech model* (Word Error Rate). Microsoft Learn,
   Azure AI Speech. https://learn.microsoft.com/azure/ai-services/speech-service/how-to-custom-speech-evaluate-data
3. IEC 60601-1-8:2006+A1:2012+A2:2020. *Medical electrical equipment, Part 1-8: General
   requirements for basic safety and essential performance. Collateral standard: General
   requirements, tests and guidance for alarm systems in medical electrical equipment and medical
   electrical systems.*
4. Institute for Safe Medication Practices. *ISMP List of High-Alert Medications in Acute Care
   Settings*, 2024. ISMP Brasil. *Medicamentos potencialmente perigosos de uso hospitalar: lista
   atualizada 2019.* Boletim ISMP Brasil, v. 8, n. 1, 2019.
5. European Pressure Ulcer Advisory Panel, National Pressure Injury Advisory Panel, Pan Pacific
   Pressure Injury Alliance. *Prevention and Treatment of Pressure Ulcers/Injuries: Clinical
   Practice Guideline.* 3rd ed. EPUAP/NPIAP/PPPIA, 2019.
6. Cao, Z.; Hidalgo, G.; Simon, T.; Wei, S.-E.; Sheikh, Y. OpenPose: Realtime Multi-Person 2D
   Pose Estimation Using Part Affinity Fields. *IEEE TPAMI*, 43(1), 2021.
7. Jocher, G.; Chaurasia, A.; Qiu, J. *Ultralytics YOLOv8*, 2023. https://github.com/ultralytics/ultralytics
8. Zhang, Y. et al. ByteTrack: Multi-Object Tracking by Associating Every Detection Box. *ECCV*,
   2022.
9. Lin, T.-Y. et al. Microsoft COCO: Common Objects in Context. *ECCV*, 2014.
10. Everingham, M. et al. The PASCAL Visual Object Classes (VOC) Challenge. *IJCV*, 88, 2010.
11. Johnson, A. et al. *MIMIC-IV Clinical Database Demo* (v2.2). PhysioNet, 2023.
    https://physionet.org/content/mimic-iv-demo/2.2/
12. Goldberger, A. et al. PhysioBank, PhysioToolkit, and PhysioNet. *Circulation*, 101(23), 2000.
13. Černek, A.; Sedmidubsky, J.; Budikova, P. REHAB24-6: Physical Therapy Dataset for Analyzing
    Pose Estimation Methods. *SISAP*, Springer, 2024.
14. Korfiatis, A. P. et al. PriMock57: A Dataset of Primary Care Mock Consultations. *ACL*, 2022.
15. Microsoft. *Text Analytics for health* e *Sentiment analysis*. Microsoft Learn, Azure AI Language.
16. Iglewicz, B.; Hoaglin, D. *How to Detect and Handle Outliers* (z-score modificado com MAD).
    ASQC, 1993.
17. Brasil. Lei nº 13.709/2018 (Lei Geral de Proteção de Dados Pessoais). ANVISA, RDC nº 657/2022
    (software como dispositivo médico).
18. Boersma, P.; Weenink, D. *Praat: doing phonetics by computer.* https://www.praat.org
19. Jadoul, Y.; Thompson, B.; de Boer, B. Introducing Parselmouth: A Python interface to Praat.
    *Journal of Phonetics*, 71, 1-15, 2018. https://doi.org/10.1016/j.wocn.2018.07.001
20. Gemmeke, J. F. et al. Audio Set: An ontology and human-labeled dataset for audio events. *IEEE
    ICASSP*, 2017.
21. Plakal, M.; Ellis, D. *YAMNet.* TensorFlow Models, `research/audioset/yamnet`.
    https://github.com/tensorflow/models/tree/master/research/audioset/yamnet
22. Teixeira, J. P.; Oliveira, C.; Lopes, C. Vocal acoustic analysis: jitter, shimmer and HNR
    parameters. *Procedia Technology*, 9, 1112-1122, 2013.
