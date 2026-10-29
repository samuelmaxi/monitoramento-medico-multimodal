# Rótulos de queda da US17

`quedas.csv` rotula os 40 clipes de leito do FallVision que estão em
`conteudos/videos/` (baixados por `scripts/get_content_script.py`):

| coluna | significado |
| --- | --- |
| `video_id` | nome do arquivo sem extensão |
| `queda` | `1` = a pessoa cai do leito para o chão no clipe; `0` = fica deitada no leito |
| `grupo` | clipes que são o mesmo vídeo reencodado (mesma cena, mesmas detecções) têm o mesmo grupo e caem na mesma partição |

**Como foram rotulados:** inspeção visual de 8 quadros por clipe (folha de
contato), sem usar a saída do detector. Todos os `B_D_*` mostram a queda do
leito ao chão e todos os `B_N_*` mostram a pessoa deitada sem sair do leito.
Não confirmamos o significado dos prefixos na documentação do FallVision.
**Precisa de conferência humana** antes de valer como verdade de referência.

Duplicados (mesma cena reencodada): `B_D_0003/0004`, `B_D_0011/0012`,
`B_D_0017/0018`, `B_D_0019/0020`, `B_D_0025/0026`.

A partição (calibração × teste) não está no arquivo: é calculada pela paridade
do MD5 do `grupo` em `movimentacao/avaliacao.py::particao`, a mesma regra da US08.
