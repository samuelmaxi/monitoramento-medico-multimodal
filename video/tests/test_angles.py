"""Testes do cálculo de ângulos e do tratamento de keypoints null (US06).

Não dependem dos pesos do OpenPose: a pose é montada à mão.
"""

from __future__ import annotations

import math

import pytest

from video.pose.angles import (
    ANGLE_COLUMNS,
    compute_angles,
    frame_angles,
    hip_center,
    inclination_from_vertical,
    joint_angle,
)
from video.pose.extract import _apply_threshold
from video.pose.sequence import PoseFrame, PoseSequence


def _seq(*keypoint_dicts, min_valid=8):
    frames = [PoseFrame(i * 3, i * 100, kps) for i, kps in enumerate(keypoint_dicts)]
    names = sorted({n for kps in keypoint_dicts for n in kps})
    return PoseSequence("teste", "teste.mp4", names, {"min_valid_keypoints": min_valid}, frames)


def _kp(x, y, conf=0.9):
    return [x, y, conf]


# Pose em pé, de frente, em pixels (y cresce para baixo). Perna esquerda
# esticada, joelho direito a 90°, braços com ângulos conhecidos.
STANDING = {
    "Neck": _kp(100, 50),
    "MidHip": _kp(100, 150),
    "LShoulder": _kp(130, 50), "RShoulder": _kp(70, 50),
    "LElbow": _kp(130, 100), "RElbow": _kp(20, 50),
    "LWrist": _kp(130, 150), "RWrist": _kp(20, 0),
    "LHip": _kp(130, 150), "RHip": _kp(70, 150),
    "LKnee": _kp(130, 200), "RKnee": _kp(70, 200),
    "LAnkle": _kp(130, 250), "RAnkle": _kp(120, 200),
}


class TestJointAngle:
    def test_angulo_reto(self):
        assert joint_angle((1, 0), (0, 0), (0, 1)) == pytest.approx(90.0)

    def test_membro_esticado_180(self):
        assert joint_angle((0, 0), (0, 10), (0, 20)) == pytest.approx(180.0)

    def test_45_graus(self):
        assert joint_angle((1, 0), (0, 0), (1, 1)) == pytest.approx(45.0)

    def test_segmentos_sobrepostos_0(self):
        assert joint_angle((2, 0), (0, 0), (5, 0)) == pytest.approx(0.0)

    def test_independe_de_escala_e_translacao(self):
        a = joint_angle((1, 0), (0, 0), (1, math.sqrt(3)))
        b = joint_angle((510, 300), (500, 300), (510, 300 + 10 * math.sqrt(3)))
        assert a == pytest.approx(60.0)
        assert b == pytest.approx(60.0)

    def test_aceita_keypoint_com_confianca(self):
        # Keypoints vêm como [x, y, conf]; a confiança é ignorada na geometria.
        assert joint_angle([1, 0, 0.4], [0, 0, 0.9], [0, 1, 0.5]) == pytest.approx(90.0)

    @pytest.mark.parametrize("a, v, c", [
        (None, (0, 0), (0, 1)),
        ((1, 0), None, (0, 1)),
        ((1, 0), (0, 0), None),
    ])
    def test_keypoint_null_da_nan(self, a, v, c):
        assert math.isnan(joint_angle(a, v, c))

    def test_segmento_de_comprimento_zero_da_nan(self):
        assert math.isnan(joint_angle((0, 0), (0, 0), (0, 1)))


class TestInclinacaoTronco:
    def test_em_pe_0(self):
        assert inclination_from_vertical((0, 100), (0, 0)) == pytest.approx(0.0)

    def test_deitado_90(self):
        assert inclination_from_vertical((0, 0), (100, 0)) == pytest.approx(90.0)
        assert inclination_from_vertical((100, 0), (0, 0)) == pytest.approx(90.0)

    def test_inclinado_45(self):
        assert inclination_from_vertical((0, 100), (100, 0)) == pytest.approx(45.0)

    def test_de_cabeca_para_baixo_180(self):
        assert inclination_from_vertical((0, 0), (0, 100)) == pytest.approx(180.0)

    def test_null_da_nan(self):
        assert math.isnan(inclination_from_vertical(None, (0, 0)))

    def test_centro_do_quadril_sem_midhip_usa_media(self):
        kps = {"MidHip": None, "LHip": _kp(120, 150), "RHip": _kp(80, 150)}
        assert hip_center(kps) == (100, 150)

    def test_centro_do_quadril_sem_dados(self):
        assert hip_center({"MidHip": None, "LHip": _kp(1, 1), "RHip": None}) is None


class TestFrameAngles:
    def test_pose_conhecida(self):
        row = frame_angles(STANDING)
        assert row["joelho_esquerdo"] == pytest.approx(180.0)
        assert row["joelho_direito"] == pytest.approx(90.0)
        assert row["assimetria_joelho"] == pytest.approx(90.0)
        assert row["cotovelo_esquerdo"] == pytest.approx(180.0)
        assert row["cotovelo_direito"] == pytest.approx(90.0)
        assert row["assimetria_cotovelo"] == pytest.approx(90.0)
        # Braço direito aberto na horizontal, braço esquerdo junto ao corpo.
        assert row["ombro_direito"] == pytest.approx(90.0)
        assert row["ombro_esquerdo"] == pytest.approx(0.0)
        assert row["assimetria_ombro"] == pytest.approx(90.0)
        # Tronco vertical e coxa alinhada: quadril estendido.
        assert row["quadril_esquerdo"] == pytest.approx(180.0)
        assert row["inclinacao_tronco"] == pytest.approx(0.0)

    def test_keypoint_null_propaga_nan_so_onde_e_usado(self):
        kps = dict(STANDING, LAnkle=None)
        row = frame_angles(kps)
        assert math.isnan(row["joelho_esquerdo"])
        assert math.isnan(row["assimetria_joelho"])
        assert row["joelho_direito"] == pytest.approx(90.0)
        assert row["quadril_esquerdo"] == pytest.approx(frame_angles(STANDING)["quadril_esquerdo"])

    def test_keypoint_ausente_do_dicionario_equivale_a_null(self):
        kps = {k: v for k, v in STANDING.items() if k != "RElbow"}
        row = frame_angles(kps)
        assert math.isnan(row["cotovelo_direito"])
        assert math.isnan(row["ombro_direito"])
        assert math.isnan(row["assimetria_ombro"])

    def test_tronco_sem_pescoco_da_nan(self):
        assert math.isnan(frame_angles(dict(STANDING, Neck=None))["inclinacao_tronco"])


class TestComputeAngles:
    def test_colunas_e_linhas(self):
        empty = {name: None for name in STANDING}
        df = compute_angles(_seq(STANDING, empty))
        assert list(df.columns) == ["frame_idx", "timestamp_ms", *ANGLE_COLUMNS]
        assert df["frame_idx"].tolist() == [0, 3]
        assert df["timestamp_ms"].tolist() == [0, 100]
        assert df.loc[0, "joelho_direito"] == pytest.approx(90.0)
        # Quadro sem nenhum keypoint: todos os ângulos NaN, sem exceção.
        assert df.loc[1, ANGLE_COLUMNS].isna().all()

    def test_sequencia_vazia(self):
        df = compute_angles(_seq())
        assert df.empty
        assert list(df.columns) == ["frame_idx", "timestamp_ms", *ANGLE_COLUMNS]


class TestNullEDeteccao:
    def test_limiar_de_confianca(self):
        raw = [(10.0, 20.0, 0.29), (10.0, 20.0, 0.3), None, (1.234, 5.678, 0.91234)]
        kps = _apply_threshold(raw, ["A", "B", "C", "D"], 0.3)
        assert kps["A"] is None          # abaixo do limiar
        assert kps["B"] == [10.0, 20.0, 0.3]  # igual ao limiar é mantido
        assert kps["C"] is None          # não detectado
        assert kps["D"] == [1.2, 5.7, 0.912]

    def test_taxa_de_deteccao_usa_minimo_de_keypoints(self):
        eight = {f"k{i}": _kp(i, i) for i in range(8)}
        seven = dict(eight, k7=None)
        seq = _seq(eight, seven, eight, seven, min_valid=8)
        assert seq.detection_rate() == pytest.approx(0.5)
        assert [seq.skeleton_detected(f) for f in seq.frames] == [True, False, True, False]

    def test_json_preserva_null(self, tmp_path):
        seq = _seq(dict(STANDING, LKnee=None))
        path = seq.save_json(tmp_path / "pose.json")
        loaded = PoseSequence.load_json(path)
        assert loaded.frames[0].keypoints["LKnee"] is None
        assert loaded.frames[0].keypoints["RKnee"] == STANDING["RKnee"]
        assert '"LKnee":null' in path.read_text()
        assert compute_angles(loaded).equals(compute_angles(seq))
