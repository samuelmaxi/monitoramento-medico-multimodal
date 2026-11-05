"""Avalia a US17 em sequências rotuladas e grava ``saida/movimentacao/avaliacao_us17.json``.

    uv run python scripts/avaliar_us17.py                  # vídeo + injeção
    uv run python scripts/avaliar_us17.py --sem-injecao    # só as quedas em vídeo

1. **Quedas em vídeo** (meta: recall ≥ 0,80 na partição de teste). Para cada
   clipe de ``data/eval/us17/quedas.csv`` em ``--videos``, roda o YOLOv8 da US07
   (parâmetros de ``video.detector`` no YAML) e aplica as regras de queda pela
   caixa. As caixas ficam em ``--cache`` para as próximas execuções. Mede as
   combinações de regras; a que vale é ``REGRAS_PADRAO`` (escolhida na
   calibração, ver ``docs/relatorio_us17_dod.md``).
2. **Casos injetados** sobre as séries reais do MIMIC-IV Demo, ``--seeds`` x
   ``--n`` casos por cenário, para os cinco tipos.
3. **Taxa de achados nos dados reais** do MIMIC (sem rótulo): achados por
   paciente-dia, para dimensionar o volume de alertas.

Sai com código 1 se o recall de queda na partição de teste ficar abaixo de 0,80.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from movimentacao.avaliacao import (  # noqa: E402
    ROTULOS,
    avaliar_casos,
    avaliar_clipes,
    ler_rotulos,
    quedas_do_clipe,
    wilson,
)
from movimentacao.config import CAMINHO_PADRAO, carregar_config  # noqa: E402
from movimentacao.injecao import gerar_casos  # noqa: E402
from movimentacao.mimic import DIRETORIO, carregar  # noqa: E402
from movimentacao.regras import detectar  # noqa: E402
from movimentacao.video import (  # noqa: E402
    REGRAS_QUEDA_PADRAO,
    CaixasDoVideo,
    caixas_do_video,
    detector_padrao,
)

META_RECALL_QUEDA = 0.80
REGRAS_PADRAO = REGRAS_QUEDA_PADRAO
COMBINACOES = {"descida": ("descida",), "caixa": ("caixa",), "descida+caixa": ("descida", "caixa")}


def _resumo(contagem, *, max_lista: int = 15) -> dict[str, object]:
    d = contagem.para_dict()
    if contagem.unidades_desvio:
        lo, hi = wilson(contagem.unidades_desvio_detectadas, contagem.unidades_desvio)
        d["recall_ic95"] = [round(lo, 3), round(hi, 3)]
    if contagem.unidades_normais:
        d["taxa_alarme_falso"] = round(
            contagem.unidades_normais_com_evento / contagem.unidades_normais, 3
        )
    for chave in ("perdidas", "alarmes_falsos"):
        if len(d[chave]) > max_lista:
            d[chave] = [*d[chave][:max_lista], f"... +{len(d[chave]) - max_lista}"]
    return d


def _caixas(videos: Path, rotulos, cache: Path, cfg) -> dict[str, CaixasDoVideo]:
    guardadas = json.loads(cache.read_text()) if cache.is_file() else {}
    detector = None
    resultado = {}
    for r in rotulos:
        if r.video_id not in guardadas:
            arquivo = videos / f"{r.video_id}.mp4"
            if not arquivo.is_file():
                print(f"aviso: {arquivo} não encontrado, clipe fora da avaliação", file=sys.stderr)
                continue
            detector = detector or detector_padrao(cfg.video)
            c = caixas_do_video(arquivo, detector, fps_max=cfg.video.detector.fps_max)
            guardadas[r.video_id] = {
                "altura": c.altura, "largura": c.largura, "fps": c.fps, "caixas": c.caixas,
            }
            print(f"{r.video_id}: {len(c.caixas)} quadros com pessoa", file=sys.stderr)
        g = guardadas[r.video_id]
        resultado[r.video_id] = CaixasDoVideo(
            [tuple(x) for x in g["caixas"]], g["altura"], g["largura"], g["fps"]
        )
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(guardadas))
    return resultado


def avaliar_video(args, cfg) -> dict[str, object]:
    rotulos = ler_rotulos(args.rotulos)
    caixas = _caixas(args.videos, rotulos, args.cache, cfg)
    saida: dict[str, object] = {
        "clipes": len(caixas),
        "regras_padrao": "+".join(REGRAS_PADRAO),
        "detector": vars(cfg.video.detector),
        "queda_descida": vars(cfg.video.queda_descida),
        "queda_caixa": vars(cfg.video.queda_caixa),
        "combinacoes": {},
    }
    for nome, regras in COMBINACOES.items():
        quedas = {vid: quedas_do_clipe(c, cfg, regras) for vid, c in caixas.items()}
        por_particao = avaliar_clipes(rotulos, quedas)
        saida["combinacoes"][nome] = {p: _resumo(c) for p, c in por_particao.items()}
    return saida


def avaliar_injecao(args, cfg) -> dict[str, object]:
    series = carregar(args.mimic)
    total: dict[str, dict] = {}
    for seed in range(args.seeds):
        for tipo, por_cenario in avaliar_casos(
            gerar_casos(series, cfg, seed=seed, n_por_tipo=args.n), cfg
        ).items():
            alvo = total.setdefault(tipo, {})
            for cenario, contagem in por_cenario.items():
                if cenario in alvo:
                    alvo[cenario] += contagem
                else:
                    alvo[cenario] = contagem
    return {
        "seeds": args.seeds,
        "casos_por_cenario_e_seed": args.n,
        "por_tipo": {t: {c: _resumo(k) for c, k in v.items()} for t, v in sorted(total.items())},
    }


def taxa_real(args, cfg) -> dict[str, object]:
    series = carregar(args.mimic)
    contagem: Counter[str] = Counter()
    dias = 0.0
    for serie in series.values():
        dias += (serie[-1].instante - serie[0].instante).total_seconds() / 86400
        contagem.update(a.event_type for a in detectar(serie, cfg))
    return {
        "estadias": len(series),
        "paciente_dias": round(dias, 1),
        "achados": dict(contagem),
        "achados_por_paciente_dia": {k: round(v / dias, 2) for k, v in sorted(contagem.items())},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", type=Path, default=CAMINHO_PADRAO)
    parser.add_argument("--rotulos", type=Path, default=ROTULOS)
    parser.add_argument("--videos", type=Path, default=Path("conteudos/videos"))
    parser.add_argument(
        "--cache", type=Path, default=Path("saida/movimentacao/caixas_us17.json")
    )
    parser.add_argument("--mimic", type=Path, default=DIRETORIO)
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--sem-injecao", action="store_true")
    parser.add_argument(
        "--saida", type=Path, default=Path("saida/movimentacao/avaliacao_us17.json")
    )
    args = parser.parse_args(argv)

    cfg = carregar_config(args.config)
    resultado: dict[str, object] = {
        "versao_limiares": cfg.versao,
        "video": avaliar_video(args, cfg),
    }
    if not args.sem_injecao:
        resultado["injecao"] = avaliar_injecao(args, cfg)
        resultado["mimic_real"] = taxa_real(args, cfg)

    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")

    teste = resultado["video"]["combinacoes"]["+".join(REGRAS_PADRAO)].get("teste", {})
    recall = teste.get("recall")
    print(f"recall de queda (teste, regras {'+'.join(REGRAS_PADRAO)}): {recall}")
    print(f"resultado em {args.saida}")
    return 0 if recall is not None and recall >= META_RECALL_QUEDA else 1


if __name__ == "__main__":
    sys.exit(main())
