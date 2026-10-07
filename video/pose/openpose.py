"""Inferência do OpenPose via ``cv2.dnn`` e pós-processamento.

A rede devolve mapas de calor (um por keypoint) e Part Affinity Fields (PAFs).
O pós-processamento segue o OpenPose: picos locais nos mapas de calor viram
candidatos, os PAFs ligam candidatos em pares e os pares são agrupados em
pessoas. Devolvemos só a pessoa principal (a com mais keypoints e, no empate,
a de maior pontuação), que nos vídeos clínicos é o paciente; o REHAB24-6 tem
gravações com outra pessoa aparecendo no canto do quadro.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from .config import ModelConfig, PostprocessConfig
from .models import ModelSpec, get_model_spec

log = logging.getLogger(__name__)

# Keypoint bruto: (x, y, confiança) em pixels do quadro original.
RawKeypoint = tuple[float, float, float]


@dataclass
class _Candidate:
    part: int
    x: float
    y: float
    score: float


def _select_backend(net: cv2.dnn.Net, cfg: ModelConfig) -> str:
    cuda_ok = False
    if cfg.backend in {"auto", "cuda"}:
        try:
            cuda_ok = cv2.cuda.getCudaEnabledDeviceCount() > 0
        except cv2.error:
            cuda_ok = False
    if cfg.backend == "cuda" and not cuda_ok:
        log.warning("backend 'cuda' pedido, mas o OpenCV não tem CUDA ou não há GPU; usando CPU")
    if cuda_ok:
        net.setPreferableBackend(cv2.dnn.DNN_BACKEND_CUDA)
        target = cv2.dnn.DNN_TARGET_CUDA_FP16 if cfg.cuda_fp16 else cv2.dnn.DNN_TARGET_CUDA
        net.setPreferableTarget(target)
        return "cuda_fp16" if cfg.cuda_fp16 else "cuda"
    net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
    net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
    return "cpu"


class OpenPoseEstimator:
    """Carrega o modelo uma vez e estima a pose de quadros BGR."""

    def __init__(self, model_cfg: ModelConfig, post_cfg: PostprocessConfig):
        for path in (model_cfg.prototxt, model_cfg.weights):
            if not path.is_file():
                raise FileNotFoundError(
                    f"arquivo do modelo não encontrado: {path}. "
                    "Rode scripts/download_openpose_models.sh antes."
                )
        self.spec: ModelSpec = get_model_spec(model_cfg.name)
        self.model_cfg = model_cfg
        self.post_cfg = post_cfg
        self.net = cv2.dnn.readNetFromCaffe(str(model_cfg.prototxt), str(model_cfg.weights))
        self.backend = _select_backend(self.net, model_cfg)
        log.info("OpenPose %s carregado (backend=%s)", self.spec.name, self.backend)

    @property
    def keypoint_names(self) -> tuple[str, ...]:
        return self.spec.keypoints

    def input_size(self, frame_w: int, frame_h: int) -> tuple[int, int]:
        """Tamanho (w, h) da entrada da rede, mantendo a proporção do quadro."""
        h = self.model_cfg.input_height
        w = int(round(h * frame_w / frame_h / 16.0)) * 16
        return max(w, 16), h

    def infer_maps(self, frame: np.ndarray) -> np.ndarray:
        """Saída da rede redimensionada para o tamanho de entrada: (C, h_in, w_in)."""
        frame_h, frame_w = frame.shape[:2]
        in_w, in_h = self.input_size(frame_w, frame_h)
        # Normalização do OpenPose: pixel / 256 - 0.5, em BGR.
        blob = cv2.dnn.blobFromImage(
            frame, 1.0 / 256, (in_w, in_h), (128, 128, 128), swapRB=False, crop=False
        )
        self.net.setInput(blob)
        out = self.net.forward()[0]
        if out.shape[0] != self.spec.n_channels:
            raise RuntimeError(
                f"saída da rede com {out.shape[0]} canais; esperado {self.spec.n_channels} "
                f"para {self.spec.name}. O prototxt corresponde ao modelo configurado?"
            )
        # A rede tem passo 8; voltamos para a resolução de entrada como o OpenPose.
        maps = cv2.resize(out.transpose(1, 2, 0), (in_w, in_h), interpolation=cv2.INTER_CUBIC)
        return maps.transpose(2, 0, 1)

    def estimate(self, frame: np.ndarray) -> list[Optional[RawKeypoint]]:
        """Keypoints da pessoa principal, um por nome do modelo (None se ausente).

        A confiança é o valor do mapa de calor no pico; o corte pelo limiar de
        confiança fica a cargo de quem chama.
        """
        frame_h, frame_w = frame.shape[:2]
        maps = self.infer_maps(frame)
        in_h, in_w = maps.shape[1:]
        sx, sy = frame_w / in_w, frame_h / in_h

        candidates = self._find_peaks(maps)
        person = self._main_person(maps, candidates, in_h)

        result: list[Optional[RawKeypoint]] = [None] * len(self.spec.keypoints)
        for part, cand_idx in person.items():
            c = candidates[cand_idx]
            # +0.5: centro do pixel na entrada, levado ao quadro original.
            result[part] = ((c.x + 0.5) * sx - 0.5, (c.y + 0.5) * sy - 0.5, c.score)
        return result

    def _find_peaks(self, maps: np.ndarray) -> list[_Candidate]:
        thr = self.post_cfg.peak_threshold
        kernel = np.ones((3, 3), np.uint8)
        candidates: list[_Candidate] = []
        for part in range(len(self.spec.keypoints)):
            heat = cv2.GaussianBlur(maps[part], (3, 3), 0)
            local_max = heat == cv2.dilate(heat, kernel)
            ys, xs = np.nonzero(local_max & (heat > thr))
            for x, y in zip(xs, ys):
                candidates.append(_Candidate(part, float(x), float(y), float(maps[part, y, x])))
        return candidates

    def _connections(
        self, maps: np.ndarray, cands_a: list[int], cands_b: list[int],
        candidates: list[_Candidate], paf_xy: tuple[int, int], img_h: int,
    ) -> list[tuple[int, int, float]]:
        """Ligações aceitas entre candidatos de A e B, sem repetir candidato."""
        cfg = self.post_cfg
        paf_x = maps[self.spec.n_heatmaps + paf_xy[0]]
        paf_y = maps[self.spec.n_heatmaps + paf_xy[1]]
        h, w = paf_x.shape
        steps = np.linspace(0.0, 1.0, cfg.paf_samples)
        scored: list[tuple[float, int, int]] = []
        for ia in cands_a:
            a = candidates[ia]
            for ib in cands_b:
                b = candidates[ib]
                d = np.array([b.x - a.x, b.y - a.y])
                norm = float(np.hypot(*d))
                if norm < 1e-6:
                    continue
                u = d / norm
                xs = np.clip(np.round(a.x + steps * d[0]).astype(int), 0, w - 1)
                ys = np.clip(np.round(a.y + steps * d[1]).astype(int), 0, h - 1)
                align = paf_x[ys, xs] * u[0] + paf_y[ys, xs] * u[1]
                # Penaliza ligações mais longas que metade da altura da imagem.
                score = float(align.mean()) + min(0.5 * img_h / norm - 1.0, 0.0)
                inliers = float(np.mean(align > cfg.paf_score_threshold))
                if score > 0 and inliers >= cfg.paf_min_inlier_ratio:
                    scored.append((score, ia, ib))
        scored.sort(reverse=True)
        used_a: set[int] = set()
        used_b: set[int] = set()
        accepted = []
        for score, ia, ib in scored:
            if ia in used_a or ib in used_b:
                continue
            used_a.add(ia)
            used_b.add(ib)
            accepted.append((ia, ib, score))
        return accepted

    def _main_person(self, maps: np.ndarray, candidates: list[_Candidate], img_h: int) -> dict[int, int]:
        by_part: dict[int, list[int]] = {}
        for idx, c in enumerate(candidates):
            by_part.setdefault(c.part, []).append(idx)

        # Cada pessoa: {parte: índice do candidato} e pontuação acumulada.
        persons: list[dict[int, int]] = []
        scores: list[float] = []
        owner: dict[int, int] = {}  # candidato -> pessoa

        for (pa, pb), paf_xy in zip(self.spec.pairs, self.spec.paf_channels):
            ca, cb = by_part.get(pa, []), by_part.get(pb, [])
            if not ca or not cb:
                continue
            for ia, ib, score in self._connections(maps, ca, cb, candidates, paf_xy, img_h):
                oa, ob = owner.get(ia), owner.get(ib)
                if oa is not None and ob is not None:
                    if oa == ob:
                        continue
                    # Junta duas pessoas parciais se não houver parte em conflito.
                    if set(persons[oa]) & set(persons[ob]):
                        continue
                    for part, cidx in persons[ob].items():
                        persons[oa][part] = cidx
                        owner[cidx] = oa
                    scores[oa] += scores[ob] + score
                    persons[ob], scores[ob] = {}, 0.0
                elif oa is not None:
                    if pb in persons[oa]:
                        continue
                    persons[oa][pb] = ib
                    owner[ib] = oa
                    scores[oa] += score + candidates[ib].score
                elif ob is not None:
                    if pa in persons[ob]:
                        continue
                    persons[ob][pa] = ia
                    owner[ia] = ob
                    scores[ob] += score + candidates[ia].score
                else:
                    persons.append({pa: ia, pb: ib})
                    scores.append(score + candidates[ia].score + candidates[ib].score)
                    owner[ia] = owner[ib] = len(persons) - 1

        if not any(persons):
            # Picos soltos, sem nenhuma ligação, podem ser de pessoas diferentes;
            # misturá-los geraria ângulos falsos. Tratamos como quadro sem pessoa.
            return {}
        best = max(range(len(persons)), key=lambda i: (len(persons[i]), scores[i]))
        return persons[best]
