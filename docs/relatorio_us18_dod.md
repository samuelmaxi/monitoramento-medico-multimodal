# Relatório US18 — rastreabilidade ao Definition of Done

**US18 · Fundir achados multimodais em score de risco por paciente.** O enunciado
do Tech Challenge (Fase 4) pede, como objetivo, "realizar a análise e **fusão** de
diferentes tipos de dados médicos (texto, áudio, vídeo)" e "aplicar técnicas de
detecção de anomalias em **tempo real** para monitoramento preventivo", com
"alertas automáticos para a equipe médica". A US18 é onde as modalidades se
encontram: cada detector publica o seu achado e a fusão transforma esses achados
em um risco único por paciente, que segue para o motor de alertas (US19).

Os caminhos são relativos à raiz do repositório.

## Como rodar

```bash
uv run python scripts/demo_us18.py               # caso de demonstração até o alerta
uv run python -m fusao.run saida/eventos.jsonl   # risco por paciente a partir de um JSONL
uv run pytest tests/fusao                        # 97 testes da US18
uv run uvicorn app.main:app --reload             # GET /fusao/pacientes, POST /fusao/risco
```

## Fluxo

```text
Detectores (vídeo, áudio, texto, sinais vitais, prescrições, movimentação)
        │ EventoAchado (contrato US04)
        ▼
BarramentoLocal ──► FusaoMultimodal.receber           [assina modality ≠ fusao]
        ▲             │ buffer por patient_id, limitado à janela
        │             │ alinhar_paciente: achados cujo intervalo cruza [ate − janela, ate]
        │             │ calcular_risco: força → sinal por modalidade → concordância → noisy-OR
        │             │ republica só se o nível mudar ou o score variar ≥ 0,05
        └──────────── risco_multimodal (modality = fusao, related_event_ids = achados usados)
        │
        ▼
MotorAlertas (US19) ──► prioridade = severidade do risco ──► e-mail / webhook / painel
```

| Arquivo | Papel |
|---|---|
| `config/us18_fusao.yaml` | Todos os parâmetros: janela, pesos, eixos clínicos, níveis, emissão. Versionado |
| `fusao/config.py` | Carga e validação do YAML (chave desconhecida, peso fora de [0, 1], tipo fora do catálogo → erro com o caminho da chave) |
| `fusao/alinhamento.py` | Funções puras de alinhamento por paciente e janela |
| `fusao/score.py` | Funções puras do score, das contribuições e da concordância |
| `fusao/eventos.py` | `Risco` → `EventoAchado` (`risco_multimodal`) |
| `fusao/servico.py` | Assinante do barramento, em tempo real |
| `fusao/run.py` | CLI sobre arquivos JSONL |
| `fusao/cenarios.py` | Casos de demonstração, usados pelo script e pelos testes |
| `app/routers/fusao.py` | API `/fusao` |
| `scripts/demo_us18.py` | Demonstração ponta a ponta, até os alertas |

## Estratégia de fusão

**Late fusion com regras ponderadas.** Cada módulo já decide o próprio achado com
o modelo adequado ao seu dado (NEWS2 + z-score nos sinais vitais, Azure Text
Analytics for health no texto, YOLOv8/OpenPose no vídeo, regras ISMP nas
prescrições). A fusão combina essas **decisões**, não os dados brutos.

Por que *late* e não *early fusion* (um modelo sobre features concatenadas):

1. **Não há dado rotulado multimodal.** Um modelo aprendido precisaria de
   pacientes com vídeo, áudio, texto e sinais vitais sincronizados *e* desfecho
   rotulado. Os datasets do projeto (MIMIC-IV Demo, PriMock57, vídeos de
   fisioterapia) não são do mesmo paciente. Treinar pesos nesse cenário seria
   inventar a verdade.
2. **Modalidade ausente é o caso normal.** Um leito sem câmera ou uma consulta
   sem gravação não pode derrubar o score. Na *late fusion*, a modalidade ausente
   simplesmente não contribui.
3. **Explicabilidade.** A equipe precisa saber *por que* o risco subiu. Com
   regras, cada parcela do score aponta para um achado concreto, com o seu
   `event_id`.
4. **Independência dos módulos.** A fusão só conhece o contrato US04; cada
   integrante evolui o seu detector sem mexer nela.

### Passo a passo

Todos os números estão em `config/us18_fusao.yaml`.

**1. Força de cada achado (0–1).**

```text
forca = peso(modalidade, tipo) × valor_severidade × (1 − α + α · score) × 0,5^(idade / meia_vida)
```

- `valor_severidade`: info 0 · baixa 0,3 · média 0,6 · alta 0,9.
- `α = influencia_score = 0,3`.
- `idade` = tempo do **fim** do achado até o fim da janela (achado em curso tem idade 0).

A base é a **severidade**, e não o `score`, por uma razão medida no código: o
`score` não tem a mesma semântica entre os módulos. Nos sinais vitais é
`NEWS2 total / 20` (um NEWS2 = 8, de emergência, sai com score 0,4); no texto e
no vídeo é a confiança do modelo (um "febre" afirmado sai com 0,95). Já a
severidade é definida por cada módulo pela própria regra clínica, no mesmo
padrão IEC 60601-1-8. O `score` só modula a força dentro da faixa (±15%). Os
valores da severidade ficam no centro das faixas de
`contratos.catalogo.severidade_sugerida`.

**2. Sinal da modalidade** = maior força entre os seus achados na janela.
Máximo, e não soma: achados do mesmo sensor são correlacionados. Dez frames do
mesmo desvio de postura não são dez evidências independentes.

**3. Concordância clínica.** Se duas ou mais modalidades **diferentes** apontam
para o mesmo eixo clínico, entra um termo extra:

```text
termo_concordancia = fator (0,5) × 2ª maior força entre as modalidades do eixo
```

A 2ª maior porque a concordância só é tão forte quanto o sinal mais fraco que a
confirma. Vale o eixo de maior termo, sem somar eixos.

| Eixo | Modalidades e achados que contam |
|---|---|
| respiratório | sinais vitais (NEWS2, desvio, tendência) · áudio (`dificuldade_respiratoria`, `evento_respiratorio`, `fadiga_vocal`) · texto (`termo_critico` = "falta de ar", "dor torácica") |
| neurológico | áudio (`possivel_disartria`) · texto (AVC, confusão mental, convulsão, síncope, tontura) · vídeo (`assimetria_movimento`) · movimentação (`reducao_mobilidade`, `agitacao_subita`) |
| queda | vídeo (`mudanca_brusca_ou_queda`) · movimentação (`queda`, `saida_do_leito`, `agitacao_subita`) · prescrições (`medicamento_alta_vigilancia`: opioide/sedativo novo) · texto (tontura, síncope) |

**4. Combinação noisy-OR.**

```text
risco = 1 − Π (1 − termo_i)      termos = sinais das 6 modalidades + concordância
```

Cada termo é lido como a chance de deterioração indicada por aquela fonte; o
paciente só está bem se todas estiverem erradas. Propriedades que a DoD exige e
que os testes verificam:

- **modalidade ausente** vira fator 1: sem renormalizar, sem divisão por zero, sem falhar;
- **com dois ou mais sinais positivos, o risco é sempre maior que o maior sinal isolado**;
- o resultado fica em [0, 1] sem truncamento.

**5. Contribuição por modalidade.** O risco é repartido na proporção de
`−ln(1 − termo)`, a escala em que o noisy-OR é aditivo
(`−ln(1 − risco) = Σ −ln(1 − termo_i)`). As contribuições somam **exatamente**
o score, e uma modalidade ausente ou normal contribui 0.

**6. Nível.** `< 0,4` baixo · `0,4–0,7` médio · `≥ 0,7` alto. A severidade do
`risco_multimodal` segue o nível (baixa, media, alta), com uma exceção: com
achado em **uma modalidade só**, a fusão não acrescenta informação e aquele
achado já gerou o próprio alerta. Nesse caso o risco é publicado com severidade
`info` (aparece na API e no painel, não alerta de novo). Configurável em
`emissao.alertar_com_uma_modalidade`.

### Pesos e justificativas

O peso responde "quanto um achado desta modalidade, sozinho, indica
deterioração do paciente". A ordem segue a força da evidência: medida
fisiológica validada > evento com dano direto > relato do paciente > biomarcador
sem validação clínica isolada > sinal indireto.

| Modalidade | Peso | Justificativa |
|---|---|---|
| sinais vitais | 1,0 | O NEWS2 (Royal College of Physicians, 2017) é um escore validado de deterioração clínica, construído sobre medidas fisiológicas diretas. É a referência dos demais |
| movimentação | 0,85 | Queda e saída do leito são eventos com dano direto ao paciente, observados em registro de enfermagem e vídeo |
| texto | 0,7 | Relato do próprio paciente ("falta de ar", "dor torácica"), extraído pelo Azure Text Analytics for health com negação tratada. Subjetivo, mas clinicamente relevante |
| áudio | 0,65 | Biomarcadores vocais (pausas, jitter) são sensíveis a microfone e ambiente e não têm validação clínica como sinal isolado |
| prescrições | 0,6 | Mudança de tratamento costuma ser intencional (decisão médica); indica risco de forma indireta |
| vídeo | 0,6 | Postura e área crítica falam mais do procedimento (fisioterapia, cirurgia) do que da piora do paciente. Exceção: `mudanca_brusca_ou_queda` usa 0,85, o mesmo peso da queda registrada |

Os demais parâmetros:

| Parâmetro | Valor | Justificativa |
|---|---|---|
| `janela_min` | 60 | O NEWS2 pede no mínimo uma medição por hora para pacientes com score 5–6: a janela sempre contém ao menos uma leitura de quem está em resposta urgente |
| `meia_vida_min` | 60 | Achado do início da janela pesa metade de um achado atual. Achado em curso (imobilidade, consulta) não decai |
| `influencia_score` | 0,3 | O score só ajusta dentro da faixa da severidade, porque não é comparável entre módulos |
| `concordancia.fator` | 0,5 | Concordância reforça, mas não pode valer mais que um achado: o termo é no máximo metade do sinal confirmador |
| `niveis` | 0,4 / 0,7 | NEWS2 de resposta urgente sozinho dá 0,46 (médio); NEWS2 de emergência, 0,74 (alto). Achado médio de áudio sozinho fica em baixo (≈ 0,35) e de texto, no limite do médio (≈ 0,41): precisam de outra modalidade para chegar a alto |

**Limitação assumida.** Os pesos são uma decisão de projeto fundamentada, não um
ajuste estatístico, e precisam de validação clínica antes de uso real (como os
valores de prioridade da US19). A configuração é versionada e cada
`risco_multimodal` traz em `evidence.thresholds` a versão e os pesos com que foi
calculado, para auditoria. Com dados rotulados, o caminho natural é ajustar os
pesos por regressão logística sobre as forças por modalidade, sem mudar o
contrato nem os consumidores.

## Caso de demonstração: SpO2 em queda + fala com dispneia

`fusao/cenarios.py::cenario_dispneia` (paciente do leito UTI-07, sem vídeo e sem
movimentação). Os achados seguem o formato exato de cada módulo e são marcados
`is_synthetic=True`. Saída de `scripts/demo_us18.py`:

```text
  hora    achado                                 severidade   risco do paciente   risco_multimodal publicado
  16:10   prescricoes/sem_achados                info         0,00 baixo          sim (info)
  16:20   sinais_vitais/sem_achados              info         0,00 baixo          não (variação pequena)
  16:50   sinais_vitais/news2_resposta_urgente   media        0,46 médio          sim (info: uma modalidade)
  16:55   audio/dificuldade_respiratoria         media        0,69 médio          sim (media)
  16:56   texto/termo_critico ("falta de ar")    media        0,82 alto           sim (alta)
```

| Modalidade | Achado mais forte | Risco isolado | Contribuição no combinado |
|---|---|---|---|
| sinais vitais | `news2_resposta_urgente` (NEWS2 = 5, SpO2 92%) | 0,42 (médio) | 0,26 |
| texto | `termo_critico` "falta de ar" | 0,41 (médio) | 0,25 |
| áudio | `dificuldade_respiratoria` | 0,35 (baixo) | 0,20 |
| prescrições | `sem_achados` | 0,00 | 0,00 |
| vídeo, movimentação | ausentes | — | 0,00 |
| concordância | eixo respiratório (3 modalidades) | — | 0,11 |
| **combinado** | | **0,82 (alto)** | **0,82** |

Nenhum sinal isolado passa de médio; a combinação chega a **alto**. No motor de
alertas, cada achado isolado gera um alerta **médio** (só e-mail), e o
`risco_multimodal` gera o alerta **alto** (e-mail + webhook) com o resumo:

> Risco alto (0,82); contribuições: sinais vitais 0,26 (news2 resposta urgente),
> texto 0,25 (termo critico), áudio 0,20 (dificuldade respiratoria); concordância
> no eixo respiratório (sinais vitais + texto + áudio); sem dados de vídeo,
> movimentação

O segundo paciente da demo (UTI-03, `cenario_sem_video`) tem só sinais vitais e
prescrições. O NEWS2 = 8 dá risco 0,74 (alto) sem nenhuma falha pelas quatro
modalidades ausentes; a fusão publica com severidade `info` porque o
`news2_emergencia` já gerou o alerta alto.

## Quadro DoD → código

### 1. Achados de vídeo, áudio, texto, sinais vitais e prescrições alinhados por `patient_id` e janela temporal configurável

**Status:** ✔ implementado (inclui também movimentação).

- `fusao/alinhamento.py::alinhar_paciente` e `::alinhar`. Um achado entra quando
  o seu intervalo (`window`, ou o `timestamp`) cruza `[ate − janela, ate]`: uma
  imobilidade que começou antes da janela e continua nela conta; achado
  posterior a `ate` não conta. Eventos de outro paciente, da própria fusão e
  `event_id` repetido são descartados.
- Janela em `config/us18_fusao.yaml::janela_min`, sobrescrevível por
  `--janela-min` na CLI e `janela_min` no `POST /fusao/risco`.
- O `patient_id` é o pseudônimo HMAC da US04: determinístico, então o mesmo
  paciente casa entre modalidades sem expor o identificador.
- Em tempo real, `ate` é o fim do achado mais recente do paciente (tempo do
  evento): o replay e o streaming dão o mesmo resultado. Achado atrasado dentro
  da janela entra; fora dela, é ignorado.
- Testes: `tests/fusao/test_alinhamento.py` (18), mais `test_servico.py::test_achado_atrasado_*`.

### 2. Estratégia de fusão documentada, com pesos justificados

**Status:** ✔ implementado. Late fusion com regras ponderadas, seção
[Estratégia de fusão](#estratégia-de-fusão) acima. Pesos e parâmetros no YAML,
validados na carga (`tests/fusao/test_config.py`, 18 testes).

### 3. Score de risco por paciente (0–1 e nível baixo/médio/alto) com a contribuição de cada modalidade

**Status:** ✔ implementado.

- `fusao/score.py::calcular_risco` → `Risco` com `score`, `nivel`, `sinais`
  (força isolada e contribuição por modalidade), `concordancia`, `presentes`,
  `ausentes`.
- No evento (`fusao/eventos.py`): `evidence.features` traz `contrib_<modalidade>`
  das 6 modalidades e `contrib_concordancia` (somam o score), `forca_<modalidade>`
  (risco isolado), `score_max_isolado`, `ganho_fusao`, `modalidades_ausentes`;
  `related_event_ids` aponta os achados usados, os que definiram cada força primeiro.
- API: `GET /fusao/pacientes`, `GET /fusao/pacientes/{patient_id}`, `POST /fusao/risco`.
- Testes: `test_score.py::test_contribuicoes_somam_o_score_e_ausentes_sao_zero`
  (todas as combinações de 1, 2, 3 e 6 modalidades), `test_eventos.py::test_features_explicam_o_score`.

### 4. Funciona com modalidade ausente sem falhar

**Status:** ✔ implementado.

- Ausente vira fator 1 no noisy-OR. "Ausente" (nenhum evento) e "presente e
  normal" (`sem_achados`) dão o mesmo score, mas são reportados separadamente
  (`modalidades_ausentes` × `modalidades_presentes`).
- Testes: `test_score.py::TestModalidadeAusente` (paciente sem vídeo, só
  `sem_achados`, ausente × normal) e o paciente UTI-03 da demo.

### 5. Caso de demonstração em que a combinação eleva o risco além de cada sinal isolado

**Status:** ✔ implementado. SpO2 em queda + fala com dispneia + relato de falta
de ar: isolados 0,42 / 0,41 / 0,35, combinado 0,82 (alto). Ver a seção do caso acima.

- Testes: `test_score.py::TestCasoDeDemonstracao` e
  `test_servico.py::test_caso_de_demonstracao_vira_alerta_alta_para_a_equipe`
  (barramento → fusão → motor de alertas → alerta alta).
- Propriedade geral: `test_score.py::test_combinacao_sempre_supera_o_maior_sinal_isolado`
  (todos os 15 pares de modalidades).

### 6. Testes unitários do alinhamento temporal e do cálculo do score

**Status:** ✔ 97 testes em `tests/fusao/`, todos passando.

| Arquivo | Cobre |
|---|---|
| `test_alinhamento.py` | Bordas inclusivas da janela, intervalos longos, futuro excluído, idade, filtro por paciente, duplicatas, janela configurável, referência por paciente |
| `test_score.py` | Fórmula da força, severidade × score, peso por tipo, decaimento, noisy-OR, atribuição, níveis, máximo por modalidade, ausência, concordância e filtro de termo, severidade do risco, caso de demonstração |
| `test_config.py` | YAML do repositório e 14 configurações inválidas |
| `test_eventos.py` | Contrato US04, features, resumo, rastreabilidade, contexto e ingestão |
| `test_servico.py` | Barramento em tempo real, sem laço, republicação só com mudança, pacientes independentes, achado atrasado, buffer, isolamento de falha, integração com a US19 |
| `test_api_e_cli.py` | Endpoints `/fusao` e `python -m fusao.run` |

## Requisitos do Tech Challenge atendidos

| Enunciado (PDF da Fase 4) | Onde a US18 entra |
|---|---|
| Objetivo: "análise e fusão de diferentes tipos de dados médicos (texto, áudio, vídeo)" | Fusão de seis modalidades em um score por paciente, com contribuição de cada uma |
| Objetivo: "detecção de anomalias em tempo real para monitoramento preventivo" | `FusaoMultimodal` assina o barramento e recalcula a cada achado; o risco sobe antes de qualquer sinal isolado ser alto |
| Detecção de anomalias: "gerar alertas automáticos para a equipe médica" | O `risco_multimodal` entra no motor da US19 como qualquer achado; a prioridade segue o nível |
| Relatório técnico: "descrição do fluxo multimodal" e "resultados obtidos e exemplos de anomalias detectadas" | Este documento: fluxo, estratégia, pesos e o caso de demonstração com números |
| Vídeo: "detecção e resposta a anomalias" e "fluxo final do alerta à equipe médica" | `scripts/demo_us18.py` mostra o risco subindo achado a achado e os alertas resultantes |

## Mudança em código existente (US19)

`alertas/motor.py::_agregar`: um achado com a **mesma chave de deduplicação** e
prioridade **maior** que a do alerta aberto agora sobe a prioridade, atualiza o
resumo e reenvia. Antes, a piora ficava suprimida como ocorrência até o fim da
janela de dedup. O problema aparecia na fusão, porque o `risco_multimodal` é um
único tipo para todos os níveis: o paciente passava de risco médio para alto e a
equipe só tinha recebido o médio. A regra vale para qualquer modalidade e
continua respeitando o reconhecimento (alerta reconhecido não é alterado).
Testes: `tests/alertas/test_motor.py::TestEscalonamento::test_achado_mais_grave_*`
e `::test_achado_menos_grave_*`.

`app/main.py`: o `lifespan` liga a fusão ao mesmo barramento que os roteadores e
o motor usam. `FUSAO_CONFIG_PATH` troca o YAML (padrão `config/us18_fusao.yaml`).

## Limitações e próximos passos

- **Pesos sem validação clínica**, como dito acima. Precisam de revisão por
  profissional de saúde e, com dados rotulados, de ajuste estatístico.
- **Estado em memória.** O buffer da fusão some ao reiniciar a API e não é
  compartilhado entre workers, como o detector de sinais vitais da US15. Em
  produção, trocar por Redis ou por um Azure Stream Analytics com janela deslizante.
- **US14 (áudio) ainda não publica no barramento.** A fusão já trata os tipos de
  áudio do catálogo; o caso de demonstração usa achados de áudio sintéticos no
  formato do contrato.
- **Eixos clínicos por `event_type`.** Para o NEWS2, o eixo respiratório não
  distingue se a pontuação veio da SpO2 ou da frequência cardíaca. Refinar exige
  olhar `features` (`news2_spo2`, `news2_fr`), como já se faz com `termo` no texto.
