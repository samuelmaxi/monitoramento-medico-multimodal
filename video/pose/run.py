"""Processa um lote de vídeos: pose (JSON), ângulos (CSV) e métrica de detecção.

    python -m video.pose.run --input data/rehab24/videos --config video/pose/config.yaml

Para cada vídeo grava ``<output.dir>/<video_id>.json`` e
``<output.dir>/<video_id>_angles.csv``, e atualiza ``output.summary_file`` com a
% de quadros com esqueleto detectado. Um vídeo com erro é registrado no log e no
summary (status "erro") e o lote segue.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from .config import PoseConfig, load_config
from .extract import compute_angles, extract_pose, video_id_for
from .openpose import OpenPoseEstimator

log = logging.getLogger("video.pose.run")

SUMMARY_COLUMNS = [
    "video_id", "caminho", "status", "erro", "frames_processados",
    "frames_com_esqueleto", "pct_frames_com_esqueleto", "fps_origem",
    "fps_processado", "duracao_s", "tempo_processamento_s", "modelo", "backend",
]


def find_videos(inputs: Iterable[Path], config: PoseConfig) -> list[Path]:
    """Vídeos nas entradas (arquivos ou pastas, recursivo), sem os excluídos pelo config."""
    found: list[Path] = []
    for item in inputs:
        if item.is_dir():
            candidates = sorted(p for p in item.rglob("*") if p.is_file())
        else:
            candidates = [item]
        for path in candidates:
            if path.suffix.lower() not in config.input.extensions:
                continue
            if any(s in str(path).lower() for s in config.input.exclude_substrings):
                log.debug("ignorado pelo filtro do config: %s", path)
                continue
            found.append(path)
    return found


def process_video(
    path: Path, video_id: str, config: PoseConfig, estimator: OpenPoseEstimator,
) -> dict:
    out_dir = config.output.dir
    t0 = time.perf_counter()
    seq = extract_pose(path, config, estimator=estimator, video_id=video_id)
    seq.save_json(out_dir / f"{video_id}.json")
    compute_angles(seq).to_csv(out_dir / f"{video_id}_angles.csv", index=False, float_format="%.2f")
    elapsed = time.perf_counter() - t0

    n = len(seq.frames)
    detected = sum(seq.skeleton_detected(f) for f in seq.frames)
    meta = seq.metadata
    return {
        "video_id": video_id,
        "caminho": str(path),
        "status": "ok",
        "erro": "",
        "frames_processados": n,
        "frames_com_esqueleto": detected,
        "pct_frames_com_esqueleto": round(100.0 * detected / n, 2),
        "fps_origem": meta["source_fps"],
        "fps_processado": meta["processed_fps"],
        "duracao_s": round(meta["n_source_frames"] / meta["source_fps"], 2),
        "tempo_processamento_s": round(elapsed, 1),
        "modelo": meta["model"],
        "backend": meta["backend"],
    }


def update_summary(path: Path, rows: list[dict]) -> pd.DataFrame:
    """Mescla as linhas novas no summary existente (substitui o mesmo video_id)."""
    new = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    if path.is_file():
        old = pd.read_csv(path, dtype={"erro": str}, keep_default_na=False)
        old = old[~old["video_id"].isin(new["video_id"])]
        new = pd.concat([old, new], ignore_index=True)
    new = new.sort_values("video_id").reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    new.to_csv(path, index=False)
    return new


def _setup_logging(out_dir: Path, verbose: bool) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in (logging.StreamHandler(sys.stderr), logging.FileHandler(out_dir / "run.log", encoding="utf-8")):
        handler.setFormatter(fmt)
        root.addHandler(handler)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", nargs="+", required=True, type=Path,
                        help="pasta(s) ou arquivo(s) de vídeo; pastas são varridas recursivamente")
    parser.add_argument("--config", required=True, type=Path, help="caminho do config.yaml")
    parser.add_argument("--id-root", type=Path, default=None,
                        help="pasta base para o video_id (padrão: a própria pasta de --input; "
                             "para arquivos avulsos, o nome do arquivo)")
    parser.add_argument("--limit", type=int, default=None, help="processa no máximo N vídeos")
    parser.add_argument("--skip-existing", action="store_true",
                        help="pula vídeos que já têm JSON e CSV de ângulos na saída")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    _setup_logging(config.output.dir, args.verbose)

    jobs: list[tuple[Path, str]] = []
    for item in args.input:
        if not item.exists():
            log.error("entrada não encontrada: %s", item)
            continue
        id_root = args.id_root or (item if item.is_dir() else None)
        jobs.extend((p, video_id_for(p, id_root)) for p in find_videos([item], config))
    if args.limit is not None:
        jobs = jobs[: args.limit]
    if not jobs:
        log.error("nenhum vídeo encontrado em %s", ", ".join(map(str, args.input)))
        return 1

    ids = [vid for _, vid in jobs]
    dupes = {v for v in ids if ids.count(v) > 1}
    if dupes:
        log.error("video_id repetido (use --id-root): %s", ", ".join(sorted(dupes)))
        return 1

    estimator = OpenPoseEstimator(config.model, config.postprocess)
    rows: list[dict] = []
    for i, (path, video_id) in enumerate(jobs, 1):
        out_dir = config.output.dir
        if args.skip_existing and (out_dir / f"{video_id}.json").is_file() and (out_dir / f"{video_id}_angles.csv").is_file():
            log.info("[%d/%d] %s já processado; pulando", i, len(jobs), video_id)
            continue
        log.info("[%d/%d] processando %s", i, len(jobs), path)
        try:
            row = process_video(path, video_id, config, estimator)
            log.info("[%d/%d] %s: %d quadros, %.1f%% com esqueleto, %.1fs",
                     i, len(jobs), video_id, row["frames_processados"],
                     row["pct_frames_com_esqueleto"], row["tempo_processamento_s"])
        except Exception as exc:  # um vídeo ruim não derruba o lote
            log.exception("[%d/%d] erro ao processar %s", i, len(jobs), path)
            row = {c: "" for c in SUMMARY_COLUMNS}
            row.update(video_id=video_id, caminho=str(path), status="erro",
                       erro=f"{type(exc).__name__}: {exc}", modelo=config.model.name,
                       backend=estimator.backend)
        rows.append(row)
        # Grava a cada vídeo para não perder o progresso de lotes longos.
        update_summary(config.output.summary_file, [row])

    n_err = sum(r["status"] == "erro" for r in rows)
    log.info("concluído: %d vídeo(s), %d com erro. Summary: %s", len(rows), n_err, config.output.summary_file)
    return 0 if n_err == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
