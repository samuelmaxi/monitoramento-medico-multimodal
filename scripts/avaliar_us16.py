"""Avaliação da US16: recall e falsos positivos em casos injetados no MIMIC-IV Demo.

    python scripts/avaliar_us16.py                 # 5 sementes × 100 casos por cenário
    python scripts/avaliar_us16.py --sementes 1 --por-cenario 20

Dois modos (ver ``prescricoes/injecao.py``):

- ``controlado``: a ordem real escolhida é o regime inteiro do produto, então o
  rótulo é inequívoco. Mede se a regra é implementada como especificada.
- ``realista``: qualquer ordem agendada real, com o ruído do MIMIC em volta
  (ordens paralelas, trocas em sequência). O rótulo vem da ordem, não do regime.

Grava ``saida/prescricoes/avaliacao_us16.json`` com as métricas por tipo e
cenário, o intervalo de Wilson 95% do recall e o volume de anomalias nos dados
reais (sem rótulo).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prescricoes import MODEL_VERSION  # noqa: E402
from prescricoes.avaliacao import Contagem, avaliar  # noqa: E402
from prescricoes.config import CAMINHO_PADRAO, carregar_config  # noqa: E402
from prescricoes.injecao import Gerador  # noqa: E402
from prescricoes.mimic import DIRETORIO, carregar_internacoes, carregar_prescricoes  # noqa: E402
from prescricoes.regras import detectar  # noqa: E402

META_RECALL = 0.80


def wilson(acertos: int, total: int, z: float = 1.96) -> list[float] | None:
    if not total:
        return None
    p = acertos / total
    centro = (p + z * z / (2 * total)) / (1 + z * z / total)
    margem = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [round(centro - margem, 3), round(centro + margem, 3)]


def somar(destino: dict[str, dict[str, Contagem]], origem: dict[str, dict[str, Contagem]]) -> None:
    for tipo, cenarios in origem.items():
        for cenario, c in cenarios.items():
            d = destino.setdefault(tipo, {}).setdefault(cenario, Contagem())
            d.positivos += c.positivos
            d.detectados += c.detectados
            d.controles += c.controles
            d.falsos_positivos += c.falsos_positivos
            d.perdidos += c.perdidos
            d.alarmes_falsos += c.alarmes_falsos


def resumir(contagens: dict[str, dict[str, Contagem]]) -> dict[str, object]:
    saida: dict[str, object] = {}
    for tipo, cenarios in contagens.items():
        saida[tipo] = {}
        for cenario, c in sorted(cenarios.items(), key=lambda kv: kv[0] != "total"):
            dados = c.para_dict()
            dados["recall_ic95"] = wilson(c.detectados, c.positivos)
            dados["perdidos"] = dados["perdidos"][:10]
            dados["alarmes_falsos"] = dados["alarmes_falsos"][:10]
            saida[tipo][cenario] = dados
    total = Contagem()
    for cenarios in contagens.values():
        c = cenarios["total"]
        total.positivos += c.positivos
        total.detectados += c.detectados
        total.controles += c.controles
        total.falsos_positivos += c.falsos_positivos
    saida["geral"] = {
        **{k: v for k, v in total.para_dict().items() if k not in ("perdidos", "alarmes_falsos")},
        "recall_ic95": wilson(total.detectados, total.positivos),
    }
    return saida


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dados", type=Path, default=DIRETORIO)
    parser.add_argument("--config", type=Path, default=CAMINHO_PADRAO)
    parser.add_argument("--sementes", type=int, default=5)
    parser.add_argument("--por-cenario", type=int, default=100)
    parser.add_argument("--saida", type=Path, default=Path("saida/prescricoes/avaliacao_us16.json"))
    args = parser.parse_args(argv)

    cfg = carregar_config(args.config)
    prescricoes = carregar_prescricoes(args.dados)
    internacoes = carregar_internacoes(args.dados)

    modos = {}
    for modo, controlado in (("controlado", True), ("realista", False)):
        contagens: dict[str, dict[str, Contagem]] = {}
        for semente in range(args.sementes):
            gerador = Gerador(prescricoes, internacoes, cfg, semente=semente, controlado=controlado)
            somar(contagens, avaliar(gerador.lote(args.por_cenario), cfg, internacoes))
        modos[modo] = resumir(contagens)

    reais = detectar(prescricoes, cfg, internacoes)
    hadms = {p.internacao for p in prescricoes if p.internacao}
    resultado = {
        "model_version": MODEL_VERSION,
        "versao_limiares": cfg.versao,
        "dataset": "MIMIC-IV Clinical Database Demo 2.2 (hosp: prescriptions, pharmacy, admissions)",
        "protocolo": {
            "sementes": args.sementes,
            "casos_por_cenario_por_semente": args.por_cenario,
            "meta_recall": META_RECALL,
            "deteccao": "evento do tipo do caso citando a ordem injetada",
        },
        "modos": modos,
        "dados_reais_sem_rotulo": {
            "prescricoes": len(prescricoes),
            "internacoes": len(hadms),
            "anomalias": len(reais),
            "por_tipo_e_severidade": {
                f"{t}/{s}": n
                for (t, s), n in sorted(Counter((a.event_type, a.severidade.value) for a in reais).items())
            },
            "por_internacao_mediana": sorted(Counter(a.internacao for a in reais).values())[
                len({a.internacao for a in reais}) // 2
            ]
            if reais
            else 0,
        },
    }
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")

    for modo, resumo in modos.items():
        print(f"\n[{modo}]")
        for tipo, cenarios in resumo.items():
            if tipo == "geral":
                continue
            t = cenarios["total"]
            print(
                f"  {tipo:30} recall {t['recall']} IC95 {t['recall_ic95']}  "
                f"FP controles {t['taxa_falso_positivo']}"
            )
        g = resumo["geral"]
        print(f"  {'geral':30} recall {g['recall']} IC95 {g['recall_ic95']}  FP controles {g['taxa_falso_positivo']}")
    print(f"\n{args.saida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
