# US16 · Anomalias na evolução de prescrições: rastreabilidade ao DoD

**Fonte de verdade** (enunciado da Fase 4, "3. Detecção de Anomalias"): aplicar técnicas de detecção de anomalias na "evolução de prescrições (alterações inesperadas no tratamento)" e gerar alertas automáticos para a equipe médica. A DoD abaixo é complementar.

| Onde                                   | O quê                                                                         |
| -------------------------------------- | ----------------------------------------------------------------------------- |
| `config/us16_prescricoes.yaml`         | Limiares, exceções e lista ISMP, versionados (`versao`)                       |
| `prescricoes/mimic.py`                 | Leitura de `prescriptions` + `pharmacy` + `admissions` do MIMIC-IV Demo 2.2   |
| `prescricoes/normalizacao.py`          | Unidade, dose, via e frequência comparáveis                                   |
| `prescricoes/principios.py`            | Princípio ativo a partir do nome da farmácia                                  |
| `prescricoes/regras.py`                | Os quatro detectores                                                          |
| `prescricoes/eventos.py`               | Tradução para o `EventoAchado` (US04)                                         |
| `prescricoes/run.py`                   | CLI: `python -m prescricoes.run --baixar`                                     |
| `app/routers/prescricoes.py`          | API: `POST /prescricoes/analise`, `GET /prescricoes/mimic/{subject_id}`, `GET /prescricoes/config` |
| `postman/prescricoes.json`             | Coleção Postman (13 requisições, 41 verificações), gerada por `scripts/gerar_postman_us16.py` |
| `prescricoes/injecao.py`, `avaliacao.py`, `scripts/avaliar_us16.py` | Casos injetados e métricas                       |
| `saida/prescricoes/avaliacao_us16.json`| Resultado da avaliação (versionado)                                           |
| `tests/prescricoes/`                   | 108 testes, 21 deles da API com os corpos da coleção Postman                  |

## Itens do DoD

| # | Item | Situação | Evidência |
|---|------|----------|-----------|
| 1 | Prescrições por paciente ordenadas no tempo com medicamento, dose, unidade, via, frequência, início e fim | ✔ | `mimic.carregar_prescricoes`: 13.772 ordens, 100 pacientes e 250 internações, ordenadas por paciente e início. A frequência por extenso vem da `pharmacy` |
| 2 | Unidades normalizadas antes de qualquer comparação | ✔ | `normalizacao.interpretar_dose`: mcg/g/gm → mg, L → mL, `"25,000"` → 25000, faixa `"2-4"` → mín./máx. A dose só é comparada com a mesma unidade canônica (`test_dose_normalizada_antes_de_comparar`, `test_reducao_de_dose_e_unidades_normalizadas`) |
| 3 | Detecta mudança abrupta de dose, duplicidade, suspensão abrupta e introdução de alta vigilância | ✔ | `regras.py`, quatro `event_type` que já estavam no catálogo da modalidade `prescricoes` |
| 4 | Alta vigilância pela lista ISMP / ISMP Brasil | ✔ | `alta_vigilancia.classes` no YAML: 19 classes, entre elas insulinas, anticoagulantes, opioides, KCl concentrado (≥ 2 mEq/mL), NaCl hipertônico e bloqueadores neuromusculares |
| 5 | Justificativa legível (regra e valores antes × depois) | ✔ | `evidence.summary` e `features.antes_*`/`depois_*` em todo evento (exemplos abaixo) |
| 6 | Recall ≥ 0,80 em casos rotulados/injetados | ✔ | Realista: **0,974** (IC95 0,967–0,980); controlado: 1,00. Cada tipo fica ≥ 0,94 nos dois modos (tabela abaixo) |
| 7 | Ao menos 1 anomalia real documentada | ✔ | 4 casos reais do MIMIC-IV Demo, um de cada tipo (abaixo) |

## Como cada regra funciona

Todos os limiares estão no YAML. A tabela traz os valores da v1.0.0.

| Tipo | Dispara quando | Não dispara (exceções que vieram dos dados) |
|------|----------------|---------------------------------------------|
| `mudanca_abrupta_dose` | O regime vigente do produto, na mesma via, muda **≥ 50%** em relação ao que vigorava nas **24 h** anteriores. Compara a dose diária (dose × `doses_per_24_hrs`); sem ela, a dose por administração com a mesma frequência | Ordens PRN, de dose única, tituladas (`TITRATE TO`) ou ASDIR/escala móvel, que não têm dose fixa. O regime soma as ordens paralelas (varfarina 2,5 + 1 mg = 3,5 mg; insulina 30 UI à noite + 13 UI no café). Uma ordem antiga que termina até 6 h depois da nova é troca, não soma |
| `duplicidade_terapeutica` | Duas ordens com o mesmo princípio ativo, também dentro de combinações, em via sistêmica, sobrepostas **≥ 6 h** | Eletrólitos, soros e insulinas (reposição por protocolo e esquema basal + correção). Também ficam de fora: esquema fracionado (mesmo produto em frequências diferentes), resgate PRN do mesmo produto, bolus + manutenção (ASDIR/titulação) e vias tópicas |
| `suspensao_abrupta` | Princípio de classe que exige desmame (betabloqueador, corticoide, benzodiazepínico, opioide, clonidina, antiepiléptico, baclofeno) em uso contínuo **≥ 72 h** some de todas as ordens, sem retorno em **24 h**, com o paciente internado | Redução prévia ≥ 25% (desmame), troca por outro da mesma classe, PRN que continua, suspensão a menos de 24 h da alta ou do óbito, ordem "Discontinued via patient discharge" |
| `medicamento_alta_vigilancia` | Primeira ordem do princípio na internação, numa classe ISMP (com via e apresentação quando a lista exige: KCl só concentrado IV) | Flush/dwell de heparina, priming de máquina e ordens canceladas antes da primeira dose |

Duas correções no MIMIC foram necessárias, e as duas apareceram ao olhar os dados:

- **Troca de prescrição**: quando a ordem é alterada, o MIMIC marca a antiga como `Inactive (Due to a change order)`, mas mantém o `stoptime` original. Sem fechar a antiga no início da nova, 12 trocas apareciam como duplicidade no demo (156 em vez de 144), e a dose vigente somava as duas ordens.
- **Ordem cancelada**: 607 linhas terminam antes de começar, quase todas 1 h antes (insulina). Viram ordens de duração zero e saem da análise.

### Severidade

Na mudança de dose e na duplicidade, a severidade sobe quando o medicamento é de alta vigilância. Na introdução, ela é definida por classe no YAML e mede o que a introdução pede da equipe, não o risco do fármaco. Insulina, anticoagulante e opioide entram em quase toda internação: no demo são 55% das introduções. Com eles em "média", o demo geraria 1.416 eventos alertáveis em 250 internações (5,7 por internação), a receita da fadiga de alarme. Com a calibração atual são 640. Por isso ficam em "baixa": o evento vai para a fusão (US18) sem acionar o motor de alertas (US19). Ficam em "alta" os de erro mais grave e menos reversível: KCl concentrado, NaCl hipertônico, bloqueador neuromuscular e trombolítico.

## Avaliação (item 6)

**Protocolo** (`prescricoes/injecao.py`): cada caso parte das prescrições reais de uma internação do MIMIC-IV Demo e injeta uma ordem marcada `sintetica=True`. O detector roda sobre a internação inteira, com o ruído real em volta. Um positivo é **detectado** se sai um evento do tipo do caso citando a ordem injetada. Um controle é **falso positivo** se isso acontece onde não devia.

| Cenário | Rótulo | O que é injetado |
|---------|--------|------------------|
| `dose_aumento` / `dose_reducao` | positivo | Ordem agendada real cortada no meio e continuada com dose × 1,5–4 (ou × 0,1–0,5), até 20 h depois |
| `dose_abaixo_limiar` | controle | Mesma coisa com variação de 10–40% |
| `dose_fora_da_janela` | controle | Dose × 2, mas 30–72 h depois do fim da anterior |
| `duplicidade` | positivo | Cópia de uma ordem real (mesmo produto, ou outra via) sobrepondo 8–72 h |
| `troca_sem_sobreposicao` | controle | A mesma cópia começando quando a original termina, ou até 4 h antes |
| `suspensao` | positivo | Curso de 3,5–8 dias de medicamento que exige desmame, terminando ≥ 30 h antes da alta |
| `suspensao_com_desmame` / `suspensao_na_alta` | controle | Curso que termina com a dose reduzida à metade / até 12 h antes da alta |
| `alta_vigilancia` | positivo | Primeira ordem de 1 de 18 medicamentos ISMP ausentes da internação |
| `introducao_comum` | controle | Primeira ordem de medicamento fora da lista |

Foram 5 sementes × 100 casos por cenário, ou seja, 5.500 casos por modo (3.000 positivos e 2.500 controles). Há dois modos. No **controlado**, a ordem real escolhida é o regime inteiro do produto, então o rótulo é inequívoco. No **realista**, vale qualquer ordem agendada: ela pode ter outras em paralelo ou fazer parte de uma sequência de trocas, e o rótulo vem da ordem, não do regime. Suspensão e introdução não dependem do modo, porque injetam cursos e medicamentos novos.

| Tipo | Recall realista (IC95) | FP nos controles realista | Recall controlado (IC95) | FP controlado |
|------|-----------------------:|--------------------------:|-------------------------:|--------------:|
| `mudanca_abrupta_dose` | 0,965 (0,952–0,975) | 4,5% (45/1000) | 0,999 (0,994–1,000) | 1,1% |
| `duplicidade_terapeutica` | 0,942 (0,918–0,959) | 0% (0/500) | 1,000 (0,992–1,000) | 0% |
| `suspensao_abrupta` | 1,000 (0,992–1,000) | 0% (0/1000) | 1,000 | 0% |
| `medicamento_alta_vigilancia` | 1,000 (0,992–1,000) | 0% (0/500) | 1,000 | 0% |
| **Geral** | **0,974 (0,967–0,980)** | **1,5%** | 1,000 (0,998–1,000) | 0,4% |

Meta do projeto (recall ≥ 0,80): **atingida** em todos os tipos e nos dois modos.

**Erros no modo realista**, nenhum deles de implementação:

- *Dose perdida (35/1000)*: a ordem escolhida era uma de duas em paralelo (valproato 500 mg de manhã + 750 mg à noite). Multiplicar só uma delas muda o regime em menos de 50%, então pela regra não há mudança abrupta. Também há a dose anterior sem `doses_per_24_hrs` e com frequência diferente (levofloxacino Q48H), o que deixa as doses sem base de comparação.
- *Duplicidade perdida (29/500)*: a ordem real escolhida foi substituída depois (`Inactive (Due to a change order)`). A cópia injetada é indistinguível da ordem que a substituiu, e pela semântica do MIMIC é uma troca.
- *Dose: falso positivo nos controles (45/1000)*: a variação injetada era de 10–40%, mas outra ordem real do mesmo produto vigorava em paralelo ou terminou logo antes, e o regime mudou ≥ 50%.

**Limite da avaliação**: o rótulo é a definição da regra aplicada em dados reais, não um rótulo clínico. Os casos medem se a implementação acha esse padrão no meio do ruído do MIMIC. Não medem se cada achado real é clinicamente relevante. Para isso seria preciso a revisão de um farmacêutico clínico, e o MIMIC não traz rótulo de erro de prescrição. Nos dados reais, sem rótulo, saem 2.046 anomalias em 250 internações (mediana de 6 por internação com anomalia), das quais 640 são alertáveis (severidade ≥ média). Esse é o volume que um piloto clínico precisaria revisar.

## Anomalias reais do MIMIC-IV Demo (item 7)

Os identificadores são os pseudônimos do contrato (`PSEUDONYM_KEY` do grupo). O `pharmacy_id` permite achar as linhas no demo, que é público (ODbL). As datas são as do MIMIC, deslocadas para o futuro.

**1. Mudança abrupta de dose de anticoagulante** (`mudanca_abrupta_dose`, média, score 1,0)
`pt_472b69fdffa5894af2fbb0c0`, 2110-12-06 12:00 UTC, ordens 41490219 → 58986734
> Dose de Enoxaparin Sodium (SC) subiu 400% (40 → 200 mg/dia), 1 h depois do fim da anterior. Antes: 40 mg QPM (40 mg/dia); depois: 100 mg Q12H (200 mg/dia). Limiar: ±50% em 24 h.

Profilaxia (40 mg/dia) trocada por dose plena (1 mg/kg a cada 12 h). Pode ser intencional, como um diagnóstico de trombose, mas é exatamente a "alteração inesperada no tratamento" do enunciado: anticoagulante cinco vezes maior de uma hora para outra pede dupla checagem de peso e função renal.

**2. Duplicidade de paracetamol: combinação + isolado** (`duplicidade_terapeutica`, média, score 1,0)
`pt_b146ea87517bded06593147b`, 2157-07-17 12:00 UTC, ordens 2971770 e 4302519
> Duplicidade terapêutica: acetaminophen em Oxycodone-Acetaminophen (5mg-325mg) e em Acetaminophen, ativos em paralelo por 33 h. Já ativo: 1 TAB Q4H:PRN PO/NG; novo: 1000 mg TID (3000 mg/dia) PO/NG.

Com a combinação usada no máximo permitido (6 × 325 mg), o total chega a 4.950 mg/dia, acima do teto usual de 4 g/dia. É o caso clássico que a regra existe para pegar: o paracetamol está "escondido" na combinação.

**3. Duplicidade de opioide de liberação prolongada** (`duplicidade_terapeutica`, alta, score 1,0)
`pt_dae6d9bd506a6dcb11f3cece`, 2187-02-19 22:00 UTC, ordens 17954031 e 50014827
> Duplicidade terapêutica: Morphine SR (MS Contin) prescrito duas vezes, ativos em paralelo por 4,67 dias. Já ativo: 60 mg Q12H (120 mg/dia) PO; novo: 60 mg Q12H (120 mg/dia) PO.

São duas ordens POE distintas (`10019777-326` e `-376`), criadas com 2 dias de diferença, e nenhuma foi encerrada. Ou a dose dobrou para 240 mg/dia sem uma ordem única que dissesse isso, ou a primeira deveria ter sido suspensa.

**4. Suspensão abrupta de betabloqueador** (`suspensao_abrupta`, média)
`pt_9211c1add298b927bf8e1d2a`, 2132-12-15 08:00 UTC, ordem 90936261
> Suspensão abrupta de metoprolol (betabloqueador, exige desmame): uso contínuo por 3,25 dias, última ordem 25 mg TID (75 mg/dia) PO/NG, sem redução prévia e sem retorno ou substituto na classe em 24 h, com o paciente internado.

## Limitações

- **Princípio ativo por vocabulário**: `principios.py` cobre os 584 nomes do demo, com sinônimos de marca, sais e combinações. Um nome novo cai na heurística (primeiro termo sem forma e sal). Em produção, usaria o RxNorm (NDC → ingrediente). O `ndc` já está na tabela, mas o mapeamento NDC → RxCUI exige a base da NLM.
- **Mudança de dose não conhece o peso** do paciente nem a indicação: a enoxaparina 40 → 200 mg/dia é anomalia mesmo quando é a conduta certa. O evento pede revisão e não afirma erro.
- **Infusões tituladas ficam de fora** da mudança de dose e da duplicidade: no MIMIC a dose delas é o conteúdo da bolsa, não a velocidade. A taxa está no `inputevents` da UTI, fora do escopo da US16.
- **Vancomicina oral + IV** sai como duplicidade (10 casos). Pelo princípio ativo está certo, mas a oral age no intestino (C. difficile). Uma exceção por princípio e via resolveria.
- **Lista ISMP**: as classes foram transcritas da lista ISMP 2024 de cuidados agudos e do boletim ISMP Brasil de 2019, com os princípios presentes no demo e os mais comuns de cada classe. A instituição deve revisar a lista contra a versão vigente.
- O MIMIC registra ordens, não administrações. A suspensão é a ordem que acaba, e a dose pode ter sido suspensa antes à beira do leito (`emar`).
