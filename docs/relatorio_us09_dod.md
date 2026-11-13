# Relatório US09 — rastreabilidade ao Definition of Done

**US09 · Gerar relatório automático de desvios no procedimento.** A fonte de verdade é o
enunciado do Tech Challenge (Fase 4, "1. Análise de Vídeo"): *gerar relatórios automáticos
indicando desvios ou falhas no procedimento*. O DoD abaixo é a proposta do grupo para cumprir
esse item; cada linha aponta o código, a configuração e o *status* real.

Os caminhos são relativos à raiz do repositório.

## Fluxo

```text
US06 pose (JSON) ──┐
                   ├─► video.desvios.run.executar (US08: desvios + quadros de evidência)
US07 eventos ──────┘                │
(JSONL)                             ▼
              video/relatorio/construcao.py ─► confiança, % do tempo, modelos, limiares
                   │
                   ├─► publicar ─► barramento ─► motor de alertas (US19)   [severidade alta]
                   ├─► <fonte>_relatorio_desvios.json   (máquinas)
                   └─► <fonte>_relatorio_desvios.html   (pessoas, arquivo único)
```

A US09 **não refaz a análise**: reaproveita o resultado da US08 (`Execucao` ganhou campos
opcionais com o resultado, o perfil, a pose e os eventos de área) e acrescenta o que o DoD pede
e a US08 não tinha. A US06 e a US07 continuam etapas separadas (minutos por vídeo em CPU); a US09
roda depois delas, como a US08.

## Como reproduzir

```bash
# Leito: pose da US06 + eventos de área crítica da US07 (só um dos dois também funciona)
uv run --env-file .env python -m video.relatorio.run --perfil internacao_leito \
  --pose outputs/pose/<video>.json --eventos-areas saida/video/eventos_<video>.jsonl \
  --patient-id pt_<24 hex>

# sem o motor de alertas (os eventos ainda vão ao barramento); só JSON
uv run python -m video.relatorio.run --perfil cirurgia --eventos-areas <jsonl> \
  --source-id sala-01 --sem-alertas --formato json

uv run pytest tests/video/test_relatorio.py
```

**Sem `uv`** (por exemplo, `uv` não reconhecido no terminal): basta um ambiente leve, só com o que as
US06 a US09 usam ([`video/requirements.txt`](../video/requirements.txt)). No PowerShell, a partir da
raiz do repositório:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r video/requirements.txt
.venv\Scripts\python -m pytest tests/video/test_relatorio.py
.venv\Scripts\python -m video.relatorio.run --perfil internacao_leito --pose outputs\pose\<video>.json
```

A CLI lê o `.env` sozinha, então não precisa de `--env-file`. Esse caminho foi verificado em um
ambiente criado do zero com estes mesmos comandos (52 testes passando). `tests/video/test_api_areas.py`
e os testes da API continuam exigindo o ambiente completo (`uv sync`).

**Validação de ponta a ponta em notebook.** [`notebooks/validacao_us09.ipynb`](../notebooks/validacao_us09.ipynb)
percorre cada etapa com a saída real (entradas da US06 e da US07, desvios da US08, confiança, % do tempo,
alerta da US19, JSON, HTML), confere automaticamente os 6 itens do DoD, roda os casos de borda e a
privacidade, e termina com o esboço do relatório renderizado. Usa dados sintéticos e só grava em
`saida/validacao_us09/`. Precisa de `ipykernel` e `matplotlib` além do `requirements.txt`; a última etapa
permite apontar para a pose e os eventos de um vídeo seu. O arquivo versionado já traz as saídas da
execução (8 de 8 verificações atendidas).

A primeira célula de código confere se o kernel tem tudo (OpenCV, pandas, matplotlib…). Se faltar algo, ela
para com uma mensagem que diz **o que falta e o comando para instalar no kernel em uso**, e instala sozinha
com `INSTALAR_O_QUE_FALTAR = True`. Foi verificado em dois ambientes: o `.venv` leve e um Python global
sem OpenCV (os dois terminam com 8 de 8).

Saídas em `saida/video/desvios/` (`--saida`), ao lado das da US08:
`<fonte>_relatorio_desvios.json` e `<fonte>_relatorio_desvios.html`. Reexecutar sobrescreve.
Para PDF, abra o HTML no navegador e use *Imprimir → Salvar como PDF*. Saída da CLI: `0` = ok;
`1` = entrada ou configuração inválida; `2` = relatório gerado, mas a publicação dos eventos
falhou. Critérios de apresentação em [`config/us09_relatorio.yaml`](../config/us09_relatorio.yaml).

## Quadro DoD → código

### 1. Relatório gerado automaticamente ao final de cada vídeo (HTML, PDF ou JSON), sem edição manual

**Status:** ✔ implementado, em **JSON e HTML**. PDF não é gerado.

- Um comando (`python -m video.relatorio.run`) faz a análise, publica os eventos e grava os dois
  arquivos; ninguém edita nada. A função `video.relatorio.run.gerar` faz o mesmo para quem usa
  o código como biblioteca.
- A gravação é atômica (arquivo `.part` renomeado ao fim): nunca fica um relatório pela metade.
- **Decisão — HTML em vez de PDF.** O DoD aceita qualquer um dos três. O HTML é um arquivo único,
  sem JavaScript nem recurso externo (abre offline), com os quadros de evidência embutidos, e o
  navegador o imprime em PDF. Gerar PDF direto exigiria uma dependência nova (WeasyPrint,
  wkhtmltopdf) só para isso.
- **Não há rota na API**, como na US08: o vídeo enviado à API é apagado ao fim do job, e sem ele
  não há quadro de evidência.

### 2. Conteúdo: identificação sem dado pessoal, duração, tipo de procedimento e lista de desvios

**Status:** ✔ implementado.

| Pedido no DoD | Campo do JSON |
| --- | --- |
| Identificação do vídeo (sem dados pessoais) | `video.id` (o `source_id`) e `video.sha256` |
| Duração | `video.duracao_s` (também `quadros_analisados`, `fps`, `resolucao`) |
| Tipo de procedimento | `video.tipo_procedimento` (`fisioterapia`, `leito` ou `cirurgia`), `video.perfil` e `video.padrao_esperado` |
| Início e fim | `desvios[].inicio_s`, `fim_s`, `duracao_s` (e `inicio_utc`, `fim_utc`) |
| Tipo | `desvios[].tipo` (o `event_type` do catálogo), `tipo_rotulo` e `regra` |
| Severidade | `desvios[].severidade` (`baixa`, `media`, `alta`) |
| Confiança | `desvios[].confianca` (0 a 1), `confianca_origem`, `cobertura_trecho_pct`, `baixa_confianca` |
| Frame de evidência | `desvios[].evidencia`: `frame_index`, `tempo_s`, `uri` (e o quadro embutido no HTML) |

Além disso, o relatório traz o resumo em texto de cada desvio, a medida, o valor e o limiar que o
dispararam, as repetições contadas (fisioterapia) e os avisos de qualidade da análise.

**Decisão — o que é "confiança".** O `score` do evento mede a **intensidade** do desvio (0,5 ao
passar o limiar, 1,0 na saturação), não a certeza de que ele existe. A confiança vem da fonte da
regra e o relatório diz qual (`confianca_origem`):

- **regras de pose (OpenPose):** média da confiança dos keypoints que a medida usa, nos quadros do
  trecho do desvio. A `cobertura_trecho_pct` é a % desses quadros em que todos eles foram vistos;
- **presença em área crítica (YOLOv8):** média da confiança da detecção na entrada e na saída.

Confiança abaixo de 0,5, ou medida feita sobre menos de 50% dos quadros do trecho, marca o desvio
como `baixa_confianca`, e o relatório pede a conferência do quadro de evidência. Os dois valores
(`confianca.minima` e `cobertura_minima_pct`) são **experimentais** e estão no YAML.

**Decisão — identificação sem dado pessoal.** O relatório identifica o vídeo só pelo `source_id` e
pelo SHA-256 do arquivo. Não entram o nome do arquivo, o caminho (`C:\Users\fulano\...` já é um
nome), o ID ou pseudônimo do paciente, o leito nem a internação; quem precisa ligar o vídeo ao
paciente usa os eventos e alertas, que carregam o pseudônimo. Como rede de segurança,
`video/relatorio/privacidade.py` varre todo texto do relatório e troca CPF, e-mail e caminho de
arquivo por um marcador (o número de trocas vai em `privacidade.redacoes`).

### 3. Resumo com total de desvios por tipo e % do tempo em desvio

**Status:** ✔ implementado (`resumo`).

- `resumo.por_tipo`: total, tempo em desvio e % do vídeo de cada tipo; `resumo.por_severidade`;
  `resumo.pct_tempo_em_desvio` (e `tempo_em_desvio_s`).
- **O % une os trechos que se sobrepõem** (`video/relatorio/tempo.py`): uma queda e um giro de
  tronco, ou duas assimetrias, podem ocorrer ao mesmo tempo, e somar as durações contaria esse
  trecho duas vezes (o percentual passaria de 100%). Um desvio "até o fim do vídeo" é cortado na
  duração do vídeo. Os percentuais por tipo não somam o total, pelo mesmo motivo.
- Sem duração conhecida (perfil só de áreas, sem pose e sem o arquivo do vídeo), o percentual é
  `null` e um aviso explica; o relatório sai do mesmo jeito.

### 4. Versões dos modelos (OpenPose, YOLOv8) e limiares usados registrados

**Status:** ✔ implementado (`modelos` e `limiares`).

- **OpenPose:** nome do modelo (`BODY_25`), arquivos de pesos, altura de entrada, *backend*, limiar
  de confiança do keypoint, mínimo de keypoints válidos, versão do módulo de pose e do OpenCV, tudo
  lido do JSON da US06. **Não existe número de versão semântico para os pesos do BODY_25**: o que
  identifica o modelo é o nome, os arquivos e a versão do módulo.
- **YOLOv8:** a descrição do modelo e a versão do módulo, lidas dos eventos da US07 (ex.:
  `yolov8n@8.4.173`), mais os limiares da detecção (confiança, IoU, critério da ROI, persistência).
- **Limiares de desvio:** a versão de `config/us08_padroes.yaml`, o bloco `geral` e **o perfil
  inteiro** aplicado ao vídeo (faixas, assimetrias, repetições, queda, mudança brusca, áreas).
  Vão também a versão desta configuração (`versao_config_relatorio`) e a das regras
  (`video-desvios@…`).
- Um componente que não foi usado diz isso e por quê (`utilizado: false`, `motivo`): um vídeo
  analisado só por áreas não finge ter versão de OpenPose.

### 5. Vídeo sem desvios gera "sem desvios detectados", nunca vazio ou erro

**Status:** ✔ implementado e testado em três variações.

| Caso | `resumo.status` | Mensagem |
| --- | --- | --- |
| análise confiável, nenhum desvio | `sem_desvios_detectados` | "Sem desvios detectados." |
| análise pouco confiável (pessoa vista em poucos quadros, ou sem pose para um perfil que a exige) | `sem_desvios_detectados` | "Sem desvios detectados, mas a análise é pouco confiável: a ausência de desvios não é conclusiva." |
| pessoa nunca vista (nenhum keypoint em nenhum quadro) | idem | idem |

O relatório continua completo (identificação, padrão esperado, modelos, limiares, qualidade da
análise); só não traz tabela nem linha do tempo de desvios. No segundo caso a US08 também **não**
publica `sem_achados`, para não dizer à fusão que um vídeo mal visto está normal; o relatório
segue a mesma regra. Entrada inválida (perfil que não existe, pose ilegível) continua sendo erro:
o "nunca erro" vale para o resultado da análise, não para uma entrada quebrada.

### 6. Desvios de severidade alta enviados ao motor de alertas (US19)

**Status:** ✔ implementado e testado com o motor real e canais falsos.

- `video.relatorio.run.publicar` envia os eventos do vídeo ao barramento (`BarramentoLocal`, o
  mesmo que o motor da US19 assina). Um desvio `alta` vira alerta de prioridade `alta` (e-mail e
  webhook), sem mudar o motor: `test_desvio_de_severidade_alta_chega_ao_motor_de_alertas`. Sem
  desvios, o `sem_achados` (info) vai ao barramento para a fusão e **não** vira alerta.
- Vão ao barramento **todos** os eventos do vídeo, não só os de severidade alta: é o contrato
  (a fusão futura precisa de tudo) e quem decide o que vira alerta é o motor, por severidade
  (`alta`: e-mail e webhook; `media`: e-mail; `baixa`: só painel, em `config/alertas_regras.yaml`).
- O relatório registra o resultado em `alertas`: quantos eventos foram publicados, quantos
  desvios `alta` houve e **quais** foram ao motor (`enviados_ao_motor`, com `event_id`).
- **A publicação nunca impede o relatório.** Se o barramento falhar (disco cheio, assinante com
  erro), a falha vai ao log e ao relatório (`alertas.falhas`), `enviados_ao_motor` fica vazio
  (o relatório não afirma o que não sabe que aconteceu) e a CLI sai com `2`.
- Na CLI o motor é ligado como no `audio.vocal.run` (`construir(ConfigAlertas.from_env())`);
  `--sem-alertas` o desliga. Sem canal configurado no `.env`, o alerta fica no painel
  (`GET /alerts/dashboard`) e o envio acusa falha de canal, não do relatório.
- **Se o motor nem puder ser construído** (por exemplo, `config/alertas_regras.yaml` ausente: o
  caminho é relativo ao diretório atual, então rode da raiz do repositório), a CLI **não aborta**:
  registra `motor de alertas indisponível` no log e em `alertas.falhas`, gera o relatório e sai
  com `2`. Um desvio grave sem alerta é falha grave, e por isso fica dita, mas não leva junto o
  relatório. Verificado rodando a CLI de ponta a ponta com o motor real: a queda sintética foi ao
  barramento e virou um alerta de prioridade `alta` no painel.

## Resultado com dados reais

Os JSONs de pose da US06 não são versionados e o vídeo do dataset não está disponível, então o
relatório **não foi gerado com um desvio de pose real** neste ambiente. O que dá para rodar são os
eventos reais da US07 que o grupo versionou em `saida/video/`, com o perfil `internacao_leito`
e sem pose:

| Eventos (US07, YOLOv8n 8.4.173) | Resultado |
| --- | --- |
| `eventos_us07.jsonl` (B_D_0016: pessoa 0,3 s na lateral do leito) | `sem_desvios_detectados`, análise pouco confiável |
| `eventos_B_N_87_resized.jsonl` (nada detectado) | `sem_desvios_detectados`, análise pouco confiável |
| `eventos_B_D_0002.jsonl` (2 saídas da lateral do leito) | `sem_desvios_detectados`, análise pouco confiável |

Os três saem "pouco confiáveis" porque o perfil de leito também avalia queda e giro do tronco pela
pose, e ela não foi informada: o relatório diz isso em vez de afirmar que o vídeo está normal, que
é o comportamento desejado. Os desvios reais da US08 (queda em `B_N_03`, repetição incorreta em
`PM_001`, em [`relatorio_us08_dod.md`](relatorio_us08_dod.md)) ficam como exemplo para quando a US06
rodar de novo sobre esses vídeos.

Os desvios, a confiança, o %, o alerta e o HTML foram verificados com **pose e eventos
sintéticos** (testes) e com uma renderização do HTML conferida a olho (queda aos 4 s e pessoa na
borda do leito, em um vídeo de 12 s).

## Decisões e ressalvas

- **Gráfico.** A linha do tempo tem uma faixa por tipo de desvio. A severidade é ordinal, então
  vai em uma rampa de um só tom (`#86b6ef`, `#2a78d6`, `#104281`), validada com o
  `validate_palette.js` da skill de visualização (`--ordinal`, fundo branco): monotonia de
  luminosidade, passo entre tons, contraste do tom mais claro e tom único, **todos PASS**. Por ser
  monótona em luminosidade, a rampa sobrevive à impressão em tons de cinza. A cor não carrega o
  sentido sozinha: há legenda, a tabela de desvios logo abaixo (a "visão em tabela") e um `<title>`
  em cada barra. O relatório é um documento para impressão e fica em **tema claro**, sem modo escuro.
- **O trecho de uma queda inclui o tempo no chão.** A US08 fecha o desvio de queda quando a nova
  posição deixa de se manter, não no impacto: numa queda aos 4 s de um vídeo de 12 s em que a pessoa
  fica no chão até o fim, o desvio vai de 3,1 s a 12 s e responde por ~74% do vídeo. O número é fiel
  ao que a US08 detecta, mas "% do tempo em desvio" de uma queda mede o tempo de permanência, não
  a duração do evento.
- **`source_id` e nome de arquivo.** Sem `--source-id`, o identificador vem do nome do arquivo que a
  US06 processou. Se esse nome tiver o nome de uma pessoa, o relatório o carrega: a rede de
  segurança só reconhece CPF, e-mail e caminho, não nomes. Use `--source-id` com um código neutro.
- **Limiares de apresentação experimentais.** `confianca.minima` (0,5) e `cobertura_minima_pct`
  (50) não foram calibrados com vídeos rotulados.
- **A US08 não mudou de comportamento.** `video.desvios.run.Execucao` ganhou campos opcionais
  (`resultado`, `perfil`, `config`, `pose`, `eventos_areas`, `video`, `source_id`); a CLI, os
  eventos e o `<fonte>_relatorio.json` da US08 são os mesmos.

## Limitações

- Só vale para vídeos que passaram pela US06 e/ou pela US07; a US09 não extrai pose nem detecta
  objetos.
- Sem rota na API e sem geração direta de PDF (ver o item 1).
- A confiança de uma regra de pose usa só os keypoints da medida, e a de repetições usa os da
  medida mais mal ajustada e os da referência; não há calibração entre a confiança do OpenPose e a
  probabilidade de o desvio ser real.
- Mesmas limitações de detecção da US08 (ângulos 2D, referência contaminada, queda que depende de a
  pose ver a pessoa): o relatório as herda e as sinaliza (`analise.confiavel`, `avisos`), não as
  corrige.
- Os limiares de prioridade do motor de alertas continuam provisórios, como indica
  [`alertas_regras.yaml`](../config/alertas_regras.yaml).

## Testes

`uv run pytest tests/video/test_relatorio.py` — 52 testes, com pose, eventos e vídeo sintéticos
(sem OpenPose nem YOLOv8): configuração (inclusive recusas), união de intervalos, confiança por
fonte, cada item do DoD, os três casos "sem desvios", duração desconhecida, falha na publicação, o
motor de alertas real com canais falsos, privacidade, HTML (escape, arquivo único, evidência
embutida, linha do tempo), todos os perfis da US08 e a CLI.
