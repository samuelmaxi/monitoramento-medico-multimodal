# US17 · Alterações em arquivos que já existiam

A US17 é um pacote novo (`movimentacao/`) e quase tudo nela é arquivo novo.
Esta lista cobre **só o que já existia** no repositório (escrito em outras US ou
por outras pessoas) e foi tocado na branch
`feat/us17-detectar-anomalias-movimentacao-internacao`.

## Arquivos alterados

| Arquivo | Onde | O que mudou | Por quê |
| --- | --- | --- | --- |
| [`.gitignore`](../.gitignore) | fim do arquivo | nova entrada `dados/` | `dados/mimic-iv-demo/` (5,5 MB do `chartevents` do MIMIC-IV Demo) aparecia como não rastreado. A arquitetura já define que `dados/` fica fora do Git e é baixado por script (`python -m movimentacao.run baixar`). A branch da US15 (`feat/us15-detect-vital-sign-anomalies`) lê o mesmo arquivo e mexe no `.gitignore` em outras linhas (ela ignora `/saida/` inteiro, e a US17 grava a avaliação em `saida/movimentacao/`). No merge, quem integrar decide se `saida/` fica versionada. |
| [`.gitignore`](../.gitignore) | fim do arquivo | nova entrada `saida/movimentacao/mimic_*` | A saída da US17 sobre as 139 estadias do MIMIC (~1 MB de JSONL) pode ser regenerada a qualquer momento. Só a avaliação (`avaliacao_us17.json`) e o cache de caixas do YOLO (`caixas_us17.json`, só coordenadas) entram no Git, como as saídas de avaliação da US07. |
| [`README.md`](../README.md) | árvore de `scripts/` | linha `avaliar_us17.py` | Documentar o script novo, como as US anteriores fizeram. |
| [`README.md`](../README.md) | depois da seção "US08" | nova seção "US17 — anomalias nos padrões de movimentação durante a internação" | Como rodar a US17 (MIMIC, vídeo, avaliação), no mesmo formato da seção da US08. |
| [`docs/arquitetura/README.md`](arquitetura/README.md) | tabela de camadas, depois de "2 · Prescrições" | nova linha "2 · Movimentação" | A tabela tinha uma linha de pré-processamento por modalidade, menos movimentação, que é uma modalidade do contrato (`Modalidade.MOVIMENTACAO`). |

Nenhum arquivo `.py` de outra US foi alterado.

## Código de outras US usado sem alteração

| Módulo | US | Para quê |
| --- | --- | --- |
| `contratos/` (`EventoAchado`, `Modalidade.MOVIMENTACAO`, pseudonimização, `EmissorJsonl`, `validar_arquivo`) | US04 | Formato de saída. Os 5 `event_type` da modalidade `movimentacao` já estavam no catálogo, então o schema não mudou. |
| `video/pose/sequence.py`, `video/pose/angles.py` | US06 | Ler a pose e medir a inclinação do tronco. |
| `video/detector.py` (`DetectorYolo`, `ConfiguracaoDetector`), `video/leitor_video.py`, `video/modelos.py` | US07 | Detectar a pessoa por quadro. A US17 cria o detector com parâmetros próprios (`config/us17_movimentacao.yaml`, bloco `video.detector`: confiança 0,1, 960 px, **sem ByteTrack**) sem mudar o padrão da US07. |
| `video/desvios/run.py::ler_eventos_jsonl`, `video/desvios/avaliacao.py::Contagem` | US08 | Ler os JSONL da US07/US08 e contar recall/alarmes falsos do mesmo jeito que a US08. |

## Observações para quem mantém a US07 e a US08

Estas são constatações, não mudanças. Ficam aqui para quem for decidir sobre elas:

- **ByteTrack e pessoa deitada.** Com o padrão da US07 (confiança 0,25,
  640 px, rastreamento ligado), a pessoa aparece em 27% dos quadros dos clipes
  de queda do leito. Sem rastreamento, com confiança 0,1 e 960 px, aparece em
  58%. O tracker descarta caixas de baixa confiança, e pessoa deitada ou
  coberta é justamente o caso de baixa confiança. Pode valer a pena para o
  perfil `internacao_leito` da US08.
- **Queda saindo do leito.** A regra de queda da US08 mede a descida do quadril
  pelo OpenPose, e o relatório dela mediu recall 0,23 no FallVision. A US17 não
  rodou a pose nos clipes de leito (os pesos do OpenPose não estão no
  repositório) e mede a descida da caixa da pessoa
  (`movimentacao/video.py::quedas_por_descida`). As quedas da US08 entram na
  série quando o JSONL dela é passado em `--desvios`.
- **Clipes repetidos em `conteudos/videos/`.** `B_D_0003/0004`, `0011/0012`,
  `0017/0018`, `0019/0020` e `0025/0026` são a mesma cena reencodada (as
  detecções são idênticas). Uma avaliação que divida calibração e teste pelo
  `video_id` pode pôr cada metade do par num lado. A US17 usa uma coluna
  `grupo` (`data/eval/us17/quedas.csv`) para manter cada par junto.
