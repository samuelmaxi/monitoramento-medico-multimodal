"""Figuras com o esqueleto desenhado sobre os quadros do vídeo.

Lê a pose já extraída (``<output.dir>/<video_id>.json``, gerado por
``video.pose.run``) e salva mosaicos em ``output.figures_dir``.

    # 1 repetição correta e 1 incorreta do REHAB24-6 (usa o Segmentation.csv)
    python -m video.pose.visualize --config video/pose/config.yaml rehab24

    # 1 queda da cama do FallVision
    python -m video.pose.visualize --config video/pose/config.yaml fallvision

    # trecho qualquer de um vídeo processado
    python -m video.pose.visualize --config video/pose/config.yaml frames \\
        --video-id PM_000-Camera17-30fps --start 180 --end 377
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Optional, Sequence

import cv2
import numpy as np
import pandas as pd

from .config import PoseConfig, VisualizationConfig, load_config
from .models import get_model_spec
from .sequence import Keypoint, PoseFrame, PoseSequence

log = logging.getLogger("video.pose.visualize")

# Cores BGR: lado esquerdo do paciente em azul, direito em vermelho, centro em verde.
COLOR_LEFT = (255, 140, 0)
COLOR_RIGHT = (40, 40, 230)
COLOR_CENTER = (60, 200, 60)
# Ligações ombro-orelha existem no PAF, mas o OpenPose não as desenha.
_NOT_RENDERED = {frozenset({"RShoulder", "REar"}), frozenset({"LShoulder", "LEar"})}


def _side_color(*names: str) -> tuple[int, int, int]:
    if all(n.startswith("L") for n in names):
        return COLOR_LEFT
    if all(n.startswith("R") for n in names):
        return COLOR_RIGHT
    return COLOR_CENTER


def skeleton_edges(model_name: str) -> list[tuple[str, str]]:
    spec = get_model_spec(model_name)
    edges = [(spec.keypoints[a], spec.keypoints[b]) for a, b in spec.pairs]
    return [e for e in edges if frozenset(e) not in _NOT_RENDERED]


def draw_skeleton(
    image: np.ndarray, keypoints: dict[str, Keypoint], edges: Sequence[tuple[str, str]],
    vis: VisualizationConfig,
) -> np.ndarray:
    """Desenha segmentos e keypoints válidos (os null são omitidos)."""
    out = image.copy()
    h = out.shape[0]
    radius = max(2, int(round(h * vis.point_radius_ratio)))
    thickness = max(1, int(round(h * vis.line_thickness_ratio)))
    for a, b in edges:
        ka, kb = keypoints.get(a), keypoints.get(b)
        if ka is None or kb is None:
            continue
        cv2.line(out, (int(ka[0]), int(ka[1])), (int(kb[0]), int(kb[1])),
                 _side_color(a, b), thickness, cv2.LINE_AA)
    for name, kp in keypoints.items():
        if kp is None:
            continue
        center = (int(kp[0]), int(kp[1]))
        cv2.circle(out, center, radius, _side_color(name), -1, cv2.LINE_AA)
        cv2.circle(out, center, radius, (255, 255, 255), 1, cv2.LINE_AA)
    return out


def _put_label(img: np.ndarray, lines: Sequence[str]) -> None:
    scale = img.shape[0] / 900
    thickness = max(1, int(round(2 * scale)))
    y = int(30 * scale) + 4
    for text in lines:
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
        cv2.rectangle(img, (4, y - th - 6), (12 + tw, y + 6), (0, 0, 0), -1)
        cv2.putText(img, text, (8, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), thickness, cv2.LINE_AA)
        y += th + 14


def read_frames(video_path: Path, indices: Sequence[int]) -> dict[int, np.ndarray]:
    """Lê os quadros pedidos em ordem (sem seek, que é impreciso em alguns MP4)."""
    wanted = set(indices)
    frames: dict[int, np.ndarray] = {}
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"não foi possível abrir {video_path}")
    try:
        idx = 0
        last = max(wanted)
        while idx <= last and cap.grab():
            if idx in wanted:
                ok, frame = cap.retrieve()
                if ok:
                    frames[idx] = frame
            idx += 1
    finally:
        cap.release()
    return frames


def pick_frames(seq: PoseSequence, start: int, end: int, n: int) -> list[PoseFrame]:
    """Até ``n`` quadros processados distribuídos uniformemente em [start, end]."""
    inside = [f for f in seq.frames if start <= f.frame_idx <= end]
    if len(inside) <= n:
        return inside
    positions = np.linspace(0, len(inside) - 1, n).round().astype(int)
    return [inside[i] for i in positions]


def render_example(
    seq: PoseSequence, start: int, end: int, title: str, out_path: Path, config: PoseConfig,
) -> Path:
    vis = config.visualization
    chosen = pick_frames(seq, start, end, vis.frames_per_example)
    if not chosen:
        raise ValueError(f"{seq.video_id}: nenhum quadro processado entre {start} e {end}")
    images = read_frames(Path(seq.source_path), [f.frame_idx for f in chosen])
    edges = skeleton_edges(seq.metadata["model"])

    tiles = []
    for f in chosen:
        if f.frame_idx not in images:
            log.warning("%s: quadro %d não pôde ser lido", seq.video_id, f.frame_idx)
            continue
        img = draw_skeleton(images[f.frame_idx], f.keypoints, edges, vis)
        _put_label(img, [f"quadro {f.frame_idx}  t={f.timestamp_ms / 1000:.2f}s  kp={f.n_valid}"])
        scale = vis.tile_width / img.shape[1]
        tiles.append(cv2.resize(img, (vis.tile_width, int(round(img.shape[0] * scale))), interpolation=cv2.INTER_AREA))
    if not tiles:
        raise RuntimeError(f"{seq.video_id}: nenhum quadro lido do vídeo {seq.source_path}")

    tile_h = max(t.shape[0] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, tile_h - t.shape[0], 0, 4, cv2.BORDER_CONSTANT, value=(30, 30, 30)) for t in tiles]
    strip = np.hstack(tiles)
    header = np.full((44, strip.shape[1], 3), 30, np.uint8)
    cv2.putText(header, title, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), np.vstack([header, strip]))
    log.info("figura salva: %s", out_path)
    return out_path


def _load_sequences(config: PoseConfig, video_ids: Optional[Sequence[str]]) -> list[PoseSequence]:
    out_dir = config.output.dir
    if video_ids:
        paths = [out_dir / f"{vid}.json" for vid in video_ids]
        missing = [p for p in paths if not p.is_file()]
        if missing:
            raise FileNotFoundError(f"pose não extraída: {', '.join(map(str, missing))}. Rode video.pose.run antes.")
    else:
        paths = sorted(out_dir.glob("*.json"))
    return [PoseSequence.load_json(p) for p in paths]


def _ascii(text: str) -> str:
    # cv2.putText só desenha ASCII.
    return text.encode("ascii", "replace").decode("ascii")


def figures_rehab24(config: PoseConfig, video_ids: Optional[Sequence[str]] = None) -> list[Path]:
    """1 repetição correta e 1 incorreta, a partir dos vídeos REHAB24-6 já processados."""
    rc = config.rehab24
    seg = pd.read_csv(rc.segmentation_csv, sep=rc.csv_separator)
    seqs = [s for s in _load_sequences(config, video_ids) if re.search(r"PM_\d+", s.video_id)]
    if not seqs:
        raise FileNotFoundError("nenhum vídeo do REHAB24-6 processado em " + str(config.output.dir))

    saved: list[Path] = []
    for correctness, label in ((1, "correta"), (0, "incorreta")):
        for seq in seqs:
            pm = re.search(r"PM_\d+", seq.video_id).group(0)
            reps = seg[(seg["video_id"] == pm) & (seg["correctness"] == correctness)]
            last_processed = seq.frames[-1].frame_idx
            reps = reps[reps["last_frame"] <= last_processed]
            if reps.empty:
                continue
            rep = reps.iloc[0]
            title = _ascii(
                f"REHAB24-6 {seq.video_id} | Ex{rep['exercise_id']} rep {rep['repetition_number']} "
                f"| {label} | quadros {rep['first_frame']}-{rep['last_frame']}"
            )
            out = config.output.figures_dir / f"rehab24_{seq.video_id}_rep{int(rep['repetition_number']):02d}_{label}.jpg"
            saved.append(render_example(seq, int(rep["first_frame"]), int(rep["last_frame"]), title, out, config))
            break
        else:
            log.warning("nenhuma repetição %s encontrada nos vídeos processados", label)
    return saved


def _is_fall(seq: PoseSequence) -> bool:
    parts = Path(seq.source_path).parts
    return "Fall" in parts and "No Fall" not in parts


def figures_fallvision(config: PoseConfig, video_ids: Optional[Sequence[str]] = None) -> list[Path]:
    """Vídeo inteiro de uma queda da cama (ou dos ``video_ids`` pedidos)."""
    root = config.fallvision.root.resolve()
    seqs = [s for s in _load_sequences(config, video_ids)
            if Path(s.source_path).resolve().is_relative_to(root)]
    if not video_ids:
        seqs = [s for s in seqs if _is_fall(s) and "Bed" in Path(s.source_path).parts][:1]
    if not seqs:
        raise FileNotFoundError("nenhum vídeo do FallVision processado em " + str(config.output.dir))
    saved = []
    for seq in seqs:
        label = "queda" if _is_fall(seq) else "sem_queda"
        title = _ascii(f"FallVision {seq.video_id} | {label.replace('_', ' ')}")
        out = config.output.figures_dir / f"fallvision_{seq.video_id}_{label}.jpg"
        saved.append(render_example(seq, seq.frames[0].frame_idx, seq.frames[-1].frame_idx, title, out, config))
    return saved


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, type=Path)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_rehab = sub.add_parser("rehab24", help="repetição correta e incorreta do REHAB24-6")
    p_rehab.add_argument("--video-id", nargs="*", help="limita a estes video_id")
    p_fall = sub.add_parser("fallvision", help="queda da cama do FallVision")
    p_fall.add_argument("--video-id", nargs="*", help="video_id específicos (padrão: primeira queda da cama)")
    p_frames = sub.add_parser("frames", help="trecho de qualquer vídeo processado")
    p_frames.add_argument("--video-id", required=True)
    p_frames.add_argument("--start", type=int, required=True)
    p_frames.add_argument("--end", type=int, required=True)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config = load_config(args.config)
    if args.cmd == "rehab24":
        saved = figures_rehab24(config, args.video_id)
    elif args.cmd == "fallvision":
        saved = figures_fallvision(config, args.video_id)
    else:
        seq = _load_sequences(config, [args.video_id])[0]
        out = config.output.figures_dir / f"{args.video_id}_{args.start}-{args.end}.jpg"
        saved = [render_example(seq, args.start, args.end, _ascii(f"{args.video_id} | quadros {args.start}-{args.end}"), out, config)]
    for path in saved:
        print(path)
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())
