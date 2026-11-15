"""Avaliação da US14: recall por classe e falso positivo em voz normal, em casos injetados.

    python scripts/avaliar_us14.py                          # 3 sementes x 20 casos por classe
    python scripts/avaliar_us14.py --sementes 1 --por-classe 5

Cada caso é uma voz sintética (Praat mede jitter, shimmer, HNR e F0 de verdade) com a alteração
da classe injetada: voz normal; fala monótona, lenta e pausada (fadiga); fala curta, pausada e
soprosa com eventos respiratórios (dificuldade respiratória); jitter, shimmer e HNR alterados com
fala lenta (possível disartria). Ver ``audio/vocal/avaliacao.py``.

Mede a lógica das regras sob alterações controladas. NÃO valida os limiares clinicamente e NÃO
mede o YAMNet (os eventos respiratórios entram como scores). Grava
``saida/audio/avaliacao_us14.json``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audio.ingestao.config import carregar_config as carregar_ingestao
from audio.vocal import MODEL_VERSION
from audio.vocal.avaliacao import CLASSE_NORMAL, Contagem, avaliar, somar
from audio.vocal.config import CAMINHO_PADRAO, carregar_config
from audio.vocal.injecao import ROTULOS, gerar_casos

META_RECALL = 0.80


def wilson(acertos: int, total: int, z: float = 1.96) -> list[float] | None:
    if not total:
        return None
    p = acertos / total
    centro = (p + z * z / (2 * total)) / (1 + z * z / total)
    margem = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [round(centro - margem, 3), round(centro + margem, 3)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=CAMINHO_PADRAO)
    parser.add_argument("--config-ingestao", type=Path,
                        default=Path("audio/ingestao/config.yaml"))
    parser.add_argument("--sementes", type=int, default=3)
    parser.add_argument("--por-classe", type=int, default=20)
    parser.add_argument("--saida", type=Path, default=Path("saida/audio/avaliacao_us14.json"))
    args = parser.parse_args(argv)

    cfg = carregar_config(args.config)
    cfg_ingestao = carregar_ingestao(args.config_ingestao)
    contagens: dict[str, Contagem] = {}
    for semente in range(args.sementes):
        print(f"semente {semente + 1}/{args.sementes} ...", flush=True)
        somar(contagens, avaliar(gerar_casos(args.por_classe, semente), cfg_ingestao, cfg))

    por_rotulo = {}
    for rotulo in ROTULOS:
        c = contagens[rotulo]
        dados = c.para_dict()
        dados["recall_ic95"] = wilson(c.acertos, c.casos)
        por_rotulo[rotulo] = dados
    normais = contagens[CLASSE_NORMAL]
    classes = [r for r in ROTULOS if r != CLASSE_NORMAL]
    resultado = {
        "model_version": MODEL_VERSION,
        "versao_limiares": cfg.versao,
        "protocolo": {
            "sementes": args.sementes,
            "casos_por_classe_por_semente": args.por_classe,
            "meta_recall": META_RECALL,
            "acerto": "a classe do rótulo disparou (outras podem disparar junto); "
                      "em 'normal', nenhuma classe disparou",
            "limitacoes": [
                "voz sintética; os limiares experimentais e os parâmetros injetados são dos "
                "mesmos autores (avaliação circular quanto à validade clínica)",
                "o YAMNet não é avaliado: eventos respiratórios entram como scores injetados",
                "não é diagnóstico nem validação clínica",
            ],
        },
        "por_rotulo": por_rotulo,
        "falso_positivo_em_normal": {
            "casos": normais.casos,
            "com_alguma_classe": normais.casos - normais.acertos,
            "taxa": round((normais.casos - normais.acertos) / normais.casos, 4)
            if normais.casos else None,
        },
        "recall_medio_das_classes": round(
            sum(contagens[r].acertos / contagens[r].casos for r in classes) / len(classes), 4),
    }
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    for rotulo, d in por_rotulo.items():
        print(f"  {rotulo:26} recall {d['recall']} IC95 {d['recall_ic95']}  "
              f"principal certa {d['classe_principal_certa']}/{d['casos']}  "
              f"disparos {d['disparos']}")
    fp = resultado["falso_positivo_em_normal"]
    print(f"\n  falso positivo em voz normal: {fp['com_alguma_classe']}/{fp['casos']}")
    print(f"\n{args.saida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
