"""CLI da US17: série de movimentação → eventos ``movimentacao`` + relatório.

Registro de enfermagem do MIMIC-IV Demo (séries de dias)::

    python -m movimentacao.run baixar
    python -m movimentacao.run mimic                       # todas as estadias
    python -m movimentacao.run mimic --stay-id 32604416

Vídeo de leito (lê o que US06/US07/US08 gravaram; ``--video`` roda o YOLOv8
para a queda pela caixa da pessoa)::

    python -m movimentacao.run video --paciente 10005817 --inicio-utc 2026-10-05T19:00:00Z \\
        --pose outputs/pose/B_D_0002.json --areas saida/video/eventos_B_D_0002.jsonl \\
        --desvios saida/video/desvios/B_D_0002_eventos.jsonl --video conteudos/videos/B_D_0002.mp4

Saída em ``--saida`` (padrão ``saida/movimentacao``): ``<nome>_eventos.jsonl``
(contrato da US04) e ``<nome>_relatorio.json``. IDs de origem são
pseudonimizados com ``PSEUDONYM_KEY``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from contratos import ChavePseudonimizacaoAusente, EmissorJsonl, EventoAchado

from .config import CAMINHO_PADRAO, carregar_config
from .eventos import montar_evento, montar_sem_achados, relatorio
from .mimic import DIRETORIO, baixar, carregar
from .modelos import Observacao
from .regras import detectar

log = logging.getLogger("movimentacao")


def _gravar(saida: Path, nome: str, eventos: list[EventoAchado], rel: dict[str, Any]) -> Path:
    saida.mkdir(parents=True, exist_ok=True)
    jsonl = saida / f"{nome}_eventos.jsonl"
    jsonl.unlink(missing_ok=True)  # EmissorJsonl acrescenta; cada execução reescreve a sua saída
    EmissorJsonl(jsonl).emitir_varios(eventos)
    (saida / f"{nome}_relatorio.json").write_text(
        json.dumps(rel, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return jsonl


def _eventos_da_serie(serie: list[Observacao], cfg, **ids: Any) -> tuple[list, list[EventoAchado]]:
    anomalias = detectar(serie, cfg)
    eventos = [montar_evento(a, cfg, **ids) for a in anomalias]
    if not eventos and serie:
        eventos = [montar_sem_achados(serie, cfg, **ids)]
    return anomalias, eventos


def executar_mimic(diretorio: Path, saida: Path, config: Path, stay_id: str | None) -> Path:
    cfg = carregar_config(config)
    series = carregar(diretorio)
    if stay_id is not None:
        series = {k: v for k, v in series.items() if k[2] == stay_id}
        if not series:
            raise SystemExit(f"stay_id {stay_id} sem registros de mobilidade em {diretorio}")
    eventos: list[EventoAchado] = []
    estadias: dict[str, Any] = {}
    for (subject_id, hadm_id, sid), serie in sorted(series.items()):
        anomalias, evs = _eventos_da_serie(serie, cfg, paciente=subject_id, internacao=hadm_id)
        eventos += evs
        # O relatório fica em saida/ (fora do git); só o stay_id identifica a estadia.
        estadias[sid] = relatorio(serie, anomalias, cfg)
    rel = {
        "fonte": "MIMIC-IV Clinical Database Demo 2.2 (chartevents: Position/Turn/Activity)",
        "versao_limiares": cfg.versao,
        "estadias": len(series),
        "eventos": dict(Counter(e.event_type for e in eventos)),
        "por_severidade": dict(Counter(e.severity.value for e in eventos)),
        "por_estadia": estadias,
    }
    nome = "mimic" if stay_id is None else f"mimic_{stay_id}"
    return _gravar(saida, nome, eventos, rel)


def executar_video(
    *,
    paciente: str,
    inicio_utc: datetime,
    saida: Path,
    config: Path,
    pose: Path | None = None,
    areas: Path | None = None,
    desvios: Path | None = None,
    video: Path | None = None,
    bed_id: str | None = None,
    source_id: str | None = None,
    internacao: str | None = None,
) -> Path:
    from video.desvios.run import ler_eventos_jsonl
    from video.pose.sequence import PoseSequence

    from . import video as fonte

    cfg = carregar_config(config)
    serie: list[Observacao] = []
    nome = source_id
    if pose is not None:
        seq = PoseSequence.load_json(pose)
        serie += fonte.observacoes_de_pose(seq, inicio_utc, cfg)
        nome = nome or seq.video_id
    if areas is not None:
        serie += fonte.observacoes_de_areas(ler_eventos_jsonl(areas), cfg)
    if desvios is not None:
        serie += fonte.observacoes_de_quedas_us08(ler_eventos_jsonl(desvios))
    if video is not None:
        from video.detector import DetectorYolo

        caixas = fonte.caixas_do_video(video, DetectorYolo())
        quedas = fonte.quedas_por_caixa(caixas, cfg.video)
        serie += fonte.observacoes_de_quedas_caixa(quedas, inicio_utc)
        nome = nome or video.stem
    if not serie:
        raise SystemExit("nenhuma observação: informe --pose, --areas, --desvios e/ou --video")
    anomalias, eventos = _eventos_da_serie(
        serie, cfg, paciente=paciente, internacao=internacao, bed_id=bed_id, source_id=source_id
    )
    return _gravar(saida, nome or "video", eventos, relatorio(serie, anomalias, cfg))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", type=Path, default=CAMINHO_PADRAO)
    parser.add_argument("--saida", type=Path, default=Path("saida/movimentacao"))
    sub = parser.add_subparsers(dest="fonte", required=True)

    p_baixar = sub.add_parser("baixar", help="baixa o chartevents do MIMIC-IV Demo")
    p_baixar.add_argument("--diretorio", type=Path, default=DIRETORIO)

    p_mimic = sub.add_parser("mimic", help="registro de enfermagem do MIMIC-IV Demo")
    p_mimic.add_argument("--diretorio", type=Path, default=DIRETORIO)
    p_mimic.add_argument("--stay-id")

    p_video = sub.add_parser("video", help="vídeo de leito (US06/US07/US08)")
    p_video.add_argument("--paciente", required=True, help="pt_<24 hex> ou ID de origem")
    p_video.add_argument("--inicio-utc", required=True, help="instante do quadro 0, ISO-8601 UTC")
    p_video.add_argument("--pose", type=Path, help="JSON de pose da US06")
    p_video.add_argument("--areas", type=Path, help="JSONL de eventos da US07")
    p_video.add_argument("--desvios", type=Path, help="JSONL de eventos da US08")
    p_video.add_argument("--video", type=Path, help="vídeo, para a queda pela caixa (YOLOv8)")
    p_video.add_argument("--bed-id")
    p_video.add_argument("--source-id")
    p_video.add_argument("--internacao")

    # As opções globais também valem depois do subcomando (mimic --saida X).
    for p in (p_baixar, p_mimic, p_video):
        p.add_argument("--config", type=Path, default=argparse.SUPPRESS)
        p.add_argument("--saida", type=Path, default=argparse.SUPPRESS)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        if args.fonte == "baixar":
            log.info("chartevents em %s", baixar(args.diretorio))
            return 0
        if args.fonte == "mimic":
            jsonl = executar_mimic(args.diretorio, args.saida, args.config, args.stay_id)
        else:
            jsonl = executar_video(
                paciente=args.paciente,
                inicio_utc=datetime.fromisoformat(args.inicio_utc.replace("Z", "+00:00")),
                saida=args.saida,
                config=args.config,
                pose=args.pose,
                areas=args.areas,
                desvios=args.desvios,
                video=args.video,
                bed_id=args.bed_id,
                source_id=args.source_id,
                internacao=args.internacao,
            )
    except (ChavePseudonimizacaoAusente, FileNotFoundError) as erro:
        log.error("%s", erro)
        return 2
    log.info("eventos em %s", jsonl)
    return 0


if __name__ == "__main__":
    sys.exit(main())
