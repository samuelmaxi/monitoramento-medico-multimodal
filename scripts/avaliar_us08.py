"""Avalia os desvios da US08 contra vídeos rotulados (normal vs. desvio).

    python scripts/avaliar_us08.py --pose-dir outputs/pose outputs/pose/us08

Lê a pose já extraída pela US06 (``python -m video.pose.run``) e aplica o perfil
de cada vídeo:

- REHAB24-6 (``data/rehab24/Segmentation.csv``): cada repetição é uma unidade;
  ``correctness = 0`` é desvio. O perfil depende do exercício e da vista da
  câmera17 (tabela ``PROTOCOLO_REHAB``); combinações sem perfil ficam de fora.
  Cada bloco contínuo de repetições na mesma vista é uma sessão: o vídeo é
  recortado no trecho dele (câmera fixa por sessão). Com perfil de referência,
  as primeiras repetições da sessão são a série supervisionada e não entram na
  conta.
- FallVision (vídeos com ``Fall``/``No_Fall`` no id): cada clipe é uma unidade,
  perfil ``internacao_leito``, só eventos ``mudanca_brusca_ou_queda``.

Os limiares foram ajustados só na partição de calibração (``CALIBRACAO_REHAB``
e metade dos clipes do FallVision, pela paridade do MD5 do id); a meta da US08
é medida na partição de teste, com pessoas e clipes não vistos.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

PROTOCOLO_REHAB: dict[tuple[int, str], str] = {
    (1, "front"): "fisioterapia_abducao_ombro_frontal",
    (6, "front"): "fisioterapia_agachamento",
    (6, "half-profile"): "fisioterapia_agachamento",
}
# PM_012 entrou na calibração ao revelar que o alvo absoluto da abdução não
# generalizava; PM_038 e PM_043 foram o teste da v1.1.0 e calibram a v1.2.0.
CALIBRACAO_REHAB = frozenset({"PM_001", "PM_012", "PM_029", "PM_038", "PM_043"})
MARGEM_SESSAO_S = 2.0
PERFIL_QUEDA = "internacao_leito"
META = {"recall": 0.80, "precisao": 0.70}


def particao_fallvision(video_id: str) -> str:
    paridade = int(hashlib.md5(video_id.encode("utf-8")).hexdigest(), 16) % 2
    return "calibracao" if paridade == 0 else "teste"


def recortar(seq, inicio_s: float, fim_s: float):
    """Mesma sequência só com os quadros de [inicio_s, fim_s], com tempos absolutos."""
    from video.pose.sequence import PoseSequence

    quadros = [f for f in seq.frames if inicio_s <= f.timestamp_ms / 1000 <= fim_s]
    return PoseSequence(seq.video_id, seq.source_path, seq.keypoint_names, seq.metadata, quadros)


def main(argv: list[str] | None = None) -> int:
    raiz = Path(__file__).resolve().parent.parent
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))

    import pandas as pd

    from video.desvios.avaliacao import Contagem, Rotulo, avaliar
    from video.desvios.config import carregar_config
    from video.desvios.regras import QUEDA, analisar
    from video.pose.sequence import PoseSequence

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", type=Path, default=Path("config/us08_padroes.yaml"))
    parser.add_argument("--pose-dir", type=Path, nargs="+", default=[Path("outputs/pose")])
    parser.add_argument("--segmentacao", type=Path, default=Path("data/rehab24/Segmentation.csv"))
    parser.add_argument("--output", type=Path, default=Path("saida/video/avaliacao_us08.json"))
    args = parser.parse_args(argv)

    cfg = carregar_config(args.config)
    segmentacao = pd.read_csv(args.segmentacao, sep=";")
    poses: dict[str, Path] = {}
    for pasta in args.pose_dir:
        for caminho in sorted(pasta.glob("*.json")):
            if not caminho.name.endswith(("_angles.json", "job.json")):
                poses.setdefault(caminho.stem, caminho)

    grupos: dict[tuple[str, str], Contagem] = defaultdict(Contagem)
    detalhes: list[dict[str, object]] = []

    for video_id, caminho in sorted(poses.items()):
        rehab = re.match(r"^(PM_\d+)-Camera17", video_id)
        queda = "Fall" in video_id
        if not rehab and not queda:
            continue
        seq = PoseSequence.load_json(caminho)

        if rehab:
            pm = rehab.group(1)
            particao = "calibracao" if pm in CALIBRACAO_REHAB else "teste"
            fps = float(seq.metadata["source_fps"])
            linhas = segmentacao[segmentacao.video_id == pm].sort_values("repetition_number")
            # Sessão = bloco contínuo de repetições na mesma vista. Um vídeo pode
            # voltar a uma vista depois de outra (PM_105: frente, 45°, frente).
            bloco = (linhas.cam17_orientation != linhas.cam17_orientation.shift()).cumsum()
            for (_, exercicio, vista), reps in linhas.groupby(
                [bloco, "exercise_id", "cam17_orientation"]
            ):
                perfil_id = PROTOCOLO_REHAB.get((int(exercicio), str(vista)))
                if perfil_id is None:
                    continue
                perfil = cfg.perfil(perfil_id)
                sessao = recortar(
                    seq,
                    reps.first_frame.min() / fps - MARGEM_SESSAO_S,
                    (reps.last_frame.max() + 1) / fps + MARGEM_SESSAO_S,
                )
                referencia = perfil.repeticoes.referencia if perfil.repeticoes else None
                if referencia is not None:
                    reps = reps.iloc[referencia.repeticoes :]
                rotulos = [
                    Rotulo(
                        f"{pm}#{r.repetition_number}",
                        r.first_frame / fps,
                        (r.last_frame + 1) / fps,
                        desvio=r.correctness == 0,
                    )
                    for r in reps.itertuples()
                ]
                resultado = analisar(perfil, cfg, pose=sessao)
                contagem = avaliar(resultado.desvios, rotulos)
                grupos[(particao, perfil_id)] += contagem
                detalhes.append(
                    {
                        "video": video_id,
                        "particao": particao,
                        "perfil": perfil_id,
                        "vista": vista,
                        **contagem.para_dict(),
                    }
                )
        else:
            particao = particao_fallvision(video_id)
            resultado = analisar(cfg.perfil(PERFIL_QUEDA), cfg, pose=seq)
            caiu = not video_id.startswith("No_Fall")
            rotulo = Rotulo(video_id, 0.0, float(resultado.duracao_s or 0.0), desvio=caiu)
            contagem = avaliar(resultado.desvios, [rotulo], tipos={QUEDA})
            grupos[(particao, "queda_fallvision")] += contagem
            detalhes.append(
                {
                    "video": video_id,
                    "particao": particao,
                    "perfil": PERFIL_QUEDA,
                    "cobertura_pct": resultado.cobertura_pct,
                    **contagem.para_dict(),
                }
            )

    resumo: dict[str, dict[str, object]] = {}
    for particao in ("calibracao", "teste"):
        total = Contagem()
        por_grupo = {}
        for (p, grupo), contagem in sorted(grupos.items()):
            if p != particao:
                continue
            por_grupo[grupo] = contagem.para_dict()
            total += contagem
        resumo[particao] = {"total": total.para_dict(), "grupos": por_grupo}

    teste = resumo["teste"]["total"]
    saida = {
        "versao_limiares": cfg.versao,
        "config": str(args.config),
        "meta": META,
        "meta_atingida_no_teste": (
            teste["recall"] is not None
            and teste["precisao"] is not None
            and teste["recall"] >= META["recall"]
            and teste["precisao"] >= META["precisao"]
        ),
        "resumo": resumo,
        "videos": detalhes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")

    def fmt(valor: object) -> str:
        return "  -  " if valor is None else f"{valor:.3f}"

    print(
        f"limiares v{cfg.versao} | meta: recall ≥ {META['recall']} e precisão ≥ {META['precisao']}"
    )
    cabecalho = ("partição", "grupo", "precisão", "recall", "eventos", "desvios")
    print("{:<11} {:<38} {:>8} {:>7} {:>8} {:>8}".format(*cabecalho))
    for particao, dados in resumo.items():
        linhas = [*dados["grupos"].items(), ("TOTAL", dados["total"])]
        for grupo, m in linhas:
            print(
                f"{particao:<11} {grupo:<38} {fmt(m['precisao']):>8} {fmt(m['recall']):>7} "
                f"{m['eventos']:>8} {m['unidades_desvio_detectadas']:>3}/{m['unidades_desvio']:<4}"
            )
    print(f"meta atingida no teste: {'sim' if saida['meta_atingida_no_teste'] else 'não'}")
    print(f"detalhes: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
