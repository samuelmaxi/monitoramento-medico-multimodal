# US07 — Qualidade e seleção de frames para anotação

Data: 2026-10-08
Escopo: auditoria da seleção de frames antes da anotação manual. **Nenhum rótulo foi criado e nenhum treino foi executado.**

## 1. Objetivo

Avaliar se os 66 frames selecionados na extração `--intervalo 1.0` representam bem os
35 vídeos canônicos, se a deduplicação aHash descarta fases importantes, e comparar com
uma extração mais densa (`--intervalo 0.5`) sem sobrescrever os dados existentes.

## 2. Comparação 1.0 s vs 0.5 s

| Métrica | `--intervalo 1.0` (atual) | `--intervalo 0.5` (proposta) |
|---|---|---|
| Frames amostrados | 121 | 242 |
| Frames únicos (pós-dedup) | 66 | 79 |
| Descartados por dedup | 55 | 163 |
| Taxa de retenção | 45,5% | 32,6% |
| Grupos por split (train/val/test) | 25/7/3 | 25/7/3 |
| Frames por split (train/val/test) | 45/13/8 | 53/17/9 |

- A seleção `1.0` é **subconjunto temporal** da `0.5` (os instantes 1,0 s existem em ambas).
- Ainda assim o dedup escolhe representantes diferentes: **26 timestamps novos** aparecem
  só em `0.5`, e **13 frames** mantidos em `1.0` não são mantidos em `0.5`. Resultado
  líquido: **+13 frames únicos**.
- A divisão por grupo é **idêntica** (mesma semente, mesmos 35 grupos) → o auditor de
  vazamento continua válido; nenhum grupo cruza splits.

## 3. O dedup aHash descarta movimento real?

O aHash compara cada candidato ao último frame mantido (`Hamming <= 3` em 64 bits).
Como frames descartados não são salvos, os vídeos foram relidos e mediu-se a diferença
pixel a pixel (MAD, 0–255) do frame descartado contra o último mantido:

| Distribuição (MAD) | n | mediana | p95 | máx |
|---|---|---|---|---|
| Frames **descartados** vs. último mantido | 163 | 12,8 | 24,5 | 29,6 |
| Frames **mantidos** consecutivos | 44 | 20,0 | 47,5 | 49,1 |

- 152/163 descartes têm MAD ≥ 6; 114 ≥ 10; **60 ≥ 15**.
- Os descartes são, em média, menos diferentes que keyframes entre si — mas o MAD de ~13
  é movimento não trivial. O aHash 8×8 é uma assinatura **global de luminância**: mudanças
  localizadas (postura/posição da pessoa) quase não alteram o hash, então o frame é podado
  mesmo com a cena mudando.
- Evidência visual para conferência humana: `dataset_us07/reports/previa/dedup_flagged/`
  (50 pares lado a lado `mantido | descartado`, nome `<video>__t<ts>.jpg`, MAD anotado).

**Conclusão:** o aHash com `Hamming <= 3` é agressivo demais para detecção de pessoa/queda;
não deve ser o único critério. Números completos em `dataset_us07/reports/dedup_diagnostico.json`.

## 4. Cobertura por vídeo

Tabela completa em `dataset_us07/reports/cobertura_frames.json`.

- 28 vídeos têm ≥ 2 frames únicos em `0.5`; 5 vídeos ganham 1–2 frames novos.
- **7 vídeos com apenas 1 frame mesmo em `0.5`** (baixa cobertura):

| Vídeo | Duração | Amostrados | Únicos | MAD máx. descartado |
|---|---|---|---|---|
| B_D_0011 | 2,00 s | 4 | 1 | 10,3 |
| B_D_0013 | 1,00 s | 2 | 1 | 8,4 |
| B_D_0015 | 2,00 s | 4 | 1 | 12,5 |
| B_D_0025 | 2,00 s | 4 | 1 | 10,5 |
| B_D_0027 | 3,00 s | 6 | 1 | 13,5 |
| B_N_104 | 4,01 s | 8 | 1 | **24,4** |
| B_N_87_resized | 5,00 s | 10 | 1 | **21,8** |

  `B_N_104` e `B_N_87_resized` têm movimento relevante descartado e merecem mais frames.
  `B_D_0013` (1 s) é curto o bastante para 1 frame ser aceitável.

## 5. Pré-visualizações (inspeção humana)

- Seleção atual (66): `dataset_us07/reports/previa/` (`previa_cenario_cama.png`,
  `por_video/previa_por_video_pag1..5.png`, `indice_frames.csv`).
- Seleção proposta (79): `/tmp/opencode/us07_050/reports/previa/` (mesmos artefatos).
- Pares mantido/descartado: `dataset_us07/reports/previa/dedup_flagged/` (50 imagens).

Observação: a análise automática deste relatório não substitui a conferência visual —
confirmar nas contact sheets se as poses de queda/transição estão cobertas.

## 6. Tokens `D` e `N` (não confirmados)

Exemplos reais: `D` = 28 ocorrências (`B_D_0001..0028`); `N` = 12 (`B_N_87`, `B_N_96..106`).
Todos com `condicao = desconhecida`. Sem confirmação dos responsáveis, **não** é possível
estratificar por queda/não-queda; qualquer rótulo seria invenção.

## 7. Recomendação de seleção para anotação

Adotar `--intervalo 0.5` como base (dobra a amostragem temporal), **não** confiando no aHash
como critério único, e garantir cobertura mínima nas 7 lacunas do item 4.

| Opção | Frames | Uso |
|---|---|---|
| A (escolhida) | 109 | `0.5` + dedup + mínimo 3 frames/vídeo |
| B (mínima) | 79 | `0.5` + dedup como está |
| C (máxima) | 242 | `0.5` sem dedup (só com grande orçamento de anotação) |

**Opção A materializada** (`--intervalo 0.5 --max-frames 60 --min-frames-por-video 3`):

- 109 frames únicos. Distribuição: 3 frames → 28 vídeos, 4 → 4, 5 → 1, 2 → 2
  (`B_D_0013` com 1 s e `B_D_0024` só têm 2 frames amostrados; nenhum vídeo fica com 1).
- Divisão por grupo inalterada (25/7/3) → auditor de vazamento válido.
  Frames por split: train 76, val 23, test 10.
- Nova flag `--min-frames-por-video`: quando o dedup retém menos que o mínimo, promove
  de volta os descartes mais **diversos** (maior distância de Hamming mínima ao conjunto
  mantido), de forma determinística. Coberta por testes de regressão.
- Correção de bug: `exportar-anotacao --saida` agora deriva de `<destino>/anotavel`
  (antes gravava em `./anotavel` do cwd, ignorando `--destino`).
- Gate de treino continua **bloqueado** até haver anotações ground truth.

Artefatos da opção A: `/tmp/opencode/us07_final/` (frames, manifesto, splits, prévias,
`anotavel/` com 109 imagens + labels vazios + `pendentes_anotacao.csv`).

## 8. Arquivos gerados nesta auditoria

- `dataset_us07/reports/dedup_diagnostico.json` — MAD de descartados vs. mantidos por vídeo.
- `dataset_us07/reports/cobertura_frames.json` — cobertura por vídeo (1.0 vs 0.5).
- `dataset_us07/reports/previa/dedup_flagged/` — 50 pares mantido/descartado.
- `/tmp/opencode/us07_050/` — extração `0.5` completa (frames, manifesto, splits, prévias).
