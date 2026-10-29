from datetime import timedelta
from pathlib import Path
from typing import ClassVar

import pytest

from contratos import EventoAchado, Modalidade, Severidade
from movimentacao.modelos import Posicao as P
from movimentacao.video import (
    caixas_de_deteccoes,
    caixas_do_video,
    observacoes_de_areas,
    observacoes_de_pose,
    observacoes_de_quedas_us08,
    posicao_de_keypoints,
    quedas_por_caixa,
    quedas_por_descida,
)
from video.leitor_video import criar_video_teste
from video.modelos import Caixa, Deteccao
from video.pose.sequence import PoseFrame, PoseSequence

from .conftest import instante

EVENTOS_US07 = Path("saida/video/eventos_us07.jsonl")


def _kp(neck, hip, lsh=None, rsh=None, nose=None):
    def p(xy):
        return None if xy is None else [float(xy[0]), float(xy[1]), 0.9]

    return {"Neck": p(neck), "MidHip": p(hip), "LShoulder": p(lsh), "RShoulder": p(rsh), "Nose": p(nose)}


SENTADO = _kp((100, 100), (100, 200), (60, 100), (140, 100), (100, 70))
SUPINO = _kp((100, 100), (200, 100), (100, 60), (100, 140), (70, 100))
LATERAL_ESQ = _kp((100, 100), (200, 100), (100, 95), (102, 105), (80, 80))
LATERAL_DIR = _kp((100, 100), (200, 100), (100, 95), (102, 105), (80, 120))


def test_tronco_vertical_e_sentado(cfg):
    assert posicao_de_keypoints(SENTADO, cfg.video) is P.SENTADO


def test_deitado_com_ombros_abertos_e_supino(cfg):
    assert posicao_de_keypoints(SUPINO, cfg.video) is P.SUPINO


def test_deitado_com_ombros_sobrepostos_e_lateral(cfg):
    assert posicao_de_keypoints(LATERAL_ESQ, cfg.video) is P.LATERAL_ESQ
    assert posicao_de_keypoints(LATERAL_DIR, cfg.video) is P.LATERAL_DIR


def test_esqueleto_incompleto_e_desconhecida(cfg):
    assert posicao_de_keypoints(_kp(None, (200, 100)), cfg.video) is P.DESCONHECIDA


def test_deitado_sem_ombros_e_desconhecida(cfg):
    assert posicao_de_keypoints(_kp((100, 100), (200, 100)), cfg.video) is P.DESCONHECIDA


def _seq(quadros):
    return PoseSequence(
        video_id="v",
        source_path="v.mp4",
        keypoint_names=["Neck", "MidHip", "LShoulder", "RShoulder", "Nose"],
        metadata={"min_valid_keypoints": 2},
        frames=[PoseFrame(i, ms, kp) for i, (ms, kp) in enumerate(quadros)],
    )


def test_pose_vira_uma_observacao_por_janela(cfg):
    import dataclasses

    sem_filtro = dataclasses.replace(cfg, video=dataclasses.replace(cfg.video, persistencia_s=1))
    seq = _seq([(0, SUPINO), (300, SUPINO), (600, SENTADO), (900, SUPINO), (1000, LATERAL_ESQ), (1500, LATERAL_ESQ)])
    obs = observacoes_de_pose(seq, instante("10:00"), sem_filtro)
    assert [(o.instante, o.posicao) for o in obs] == [
        (instante("10:00"), P.SUPINO),
        (instante("10:00") + timedelta(seconds=1), P.LATERAL_ESQ),
    ]
    assert {o.fonte for o in obs} == {"video_pose"}


def test_janela_so_com_desconhecida(cfg):
    seq = _seq([(0, _kp(None, None))])
    [o] = observacoes_de_pose(seq, instante("10:00"), cfg)
    assert o.posicao is P.DESCONHECIDA


def test_area_lateral_vira_fora_do_leito(cfg):
    eventos = [EventoAchado.de_json(linha) for linha in EVENTOS_US07.read_text().splitlines()[:2]]
    assert [e.event_type for e in eventos] == ["entrada_area_critica", "saida_area_critica"]
    obs = observacoes_de_areas(eventos, cfg)
    assert [o.posicao for o in obs] == [P.FORA_DO_LEITO]
    assert obs[0].instante == eventos[0].timestamp
    assert obs[0].fonte == "video_areas"


def _evento_us08(regra: str):
    return EventoAchado.criar(
        patient_id="pt_" + "a" * 24,
        modality=Modalidade.VIDEO,
        event_type="mudanca_brusca_ou_queda",
        timestamp=instante("03:00"),
        score=0.8,
        severity=Severidade.ALTA,
        evidence={"summary": "desvio US08", "features": {"regra": regra}},
        model_version="video-desvios@0.1.0",
    )


def test_queda_da_us08_vira_chao(cfg):
    [o] = observacoes_de_quedas_us08([_evento_us08("queda"), _evento_us08("mudanca_brusca")])
    assert (o.posicao, o.queda, o.fonte) == (P.CHAO, True, "video_pose")


# --- queda pela caixa da pessoa -------------------------------------------------


def _caixas(transicao_s: float, *, volta: bool = False):
    """Em pé (100x300) até 1 s, deita (300x110) em ``transicao_s`` e fica até 3 s."""
    caixas, t = [], 0.0
    while t <= 3.0:
        if t <= 1.0 or (volta and t >= 1.0 + transicao_s + 0.2):
            w, h = 100, 300
        elif t >= 1.0 + transicao_s:
            w, h = 300, 110
        else:
            f = (t - 1.0) / transicao_s
            w, h = 100 + 200 * f, 300 - 190 * f
        caixas.append((round(t, 2), 500.0, 600.0 - h, 500.0 + w, 600.0))
        t += 0.1
    return caixas


def test_queda_por_caixa_detecta(cfg):
    quedas = quedas_por_caixa(_caixas(0.5), cfg.video)
    assert len(quedas) == 1
    assert 1.0 < quedas[0] <= 1.6


def test_deitar_devagar_nao_e_queda(cfg):
    assert quedas_por_caixa(_caixas(4.0), cfg.video) == []


def test_agachar_e_levantar_nao_e_queda(cfg):
    assert quedas_por_caixa(_caixas(0.3, volta=True), cfg.video) == []


def test_sem_caixas(cfg):
    assert quedas_por_caixa([], cfg.video) == []


def _det(frame, t, x1, y1, x2, y2, classe="person"):
    return Deteccao(0 if classe == "person" else 59, classe, 0.9, Caixa(x1, y1, x2, y2), frame, t)


def test_caixas_de_deteccoes_usa_a_maior_pessoa():
    dets = [_det(0, 0.0, 0, 0, 10, 10), _det(0, 0.0, 0, 0, 50, 100), _det(0, 0.0, 0, 0, 500, 500, "bed")]
    assert caixas_de_deteccoes(dets) == [(0.0, 0, 0, 50, 100)]


class _DetectorFalso:
    nomes_classe: ClassVar = {0: "person"}

    def detectar(self, imagem, *, indice_quadro, tempo_s):
        return [_det(indice_quadro, tempo_s, 0, 0, 10, 20)]


def test_caixas_do_video(tmp_path):
    video = criar_video_teste(tmp_path / "v.mp4", quadros=5, fps=10.0, altura=240)
    r = caixas_do_video(video, _DetectorFalso())
    assert len(r.caixas) == 5
    assert r.caixas[1][0] == pytest.approx(0.1)
    assert r.altura == 240


def test_caixas_do_video_limita_fps(tmp_path):
    video = criar_video_teste(tmp_path / "v.mp4", quadros=12, fps=60.0)
    r = caixas_do_video(video, _DetectorFalso(), fps_max=30)
    assert len(r.caixas) == 6


# --- queda pela descida da caixa (cair do leito) -----------------------------------


def _deitado_que_cai(descida_px: float, duracao_s: float, *, volta: bool = False, ate_s: float = 3.0):
    """Caixa deitada no leito (centro y=200) que desce ``descida_px`` em ``duracao_s`` a partir de 1 s."""
    caixas, t = [], 0.0
    while t <= ate_s + 1e-9:
        if t <= 1.0:
            dy = 0.0
        elif t <= 1.0 + duracao_s:
            dy = descida_px * (t - 1.0) / duracao_s
        else:
            dy = descida_px
        if volta and t >= 1.0 + duracao_s + 0.2:
            dy = 0.0
        caixas.append((round(t, 2), 100.0, 150.0 + dy, 400.0, 250.0 + dy))
        t += 0.1
    return caixas


def test_descida_do_leito_e_queda(cfg):
    # 1000 px de quadro: descer 120 px (0,12) em 0,5 s e ficar.
    quedas = quedas_por_descida(_deitado_que_cai(120, 0.5), 1000, cfg.video)
    assert len(quedas) == 1
    assert 1.0 < quedas[0] <= 1.6


def test_descida_pequena_nao_e_queda(cfg):
    assert quedas_por_descida(_deitado_que_cai(30, 0.5), 1000, cfg.video) == []


def test_descida_lenta_nao_e_queda(cfg):
    assert quedas_por_descida(_deitado_que_cai(120, 4.0, ate_s=6.0), 1000, cfg.video) == []


def test_descida_que_volta_nao_e_queda(cfg):
    assert quedas_por_descida(_deitado_que_cai(120, 0.3, volta=True), 1000, cfg.video) == []


def test_quadro_isolado_nao_e_queda(cfg):
    # Uma caixa espúria embaixo por um quadro some na mediana móvel.
    caixas = [(round(i * 0.1, 2), 100.0, 150.0, 400.0, 250.0) for i in range(30)]
    caixas[15] = (1.5, 100.0, 650.0, 400.0, 750.0)
    assert quedas_por_descida(caixas, 1000, cfg.video) == []


def test_descida_no_fim_do_clipe_conta(cfg):
    # Clipes curtos terminam logo depois do impacto: o que há depois precisa ficar embaixo.
    quedas = quedas_por_descida(_deitado_que_cai(150, 0.4, ate_s=1.6), 1000, cfg.video)
    assert len(quedas) == 1


def test_uma_queda_nao_conta_duas_vezes(cfg):
    assert len(quedas_por_descida(_deitado_que_cai(300, 0.5, ate_s=6.0), 1000, cfg.video)) == 1


def test_quedas_da_caixa_viram_observacoes():
    from movimentacao.video import observacoes_de_quedas_caixa

    [o] = observacoes_de_quedas_caixa([1.5], instante("03:00"))
    assert o.instante == instante("03:00:01") + timedelta(milliseconds=500)
    assert (o.posicao, o.queda, o.fonte) == (P.CHAO, True, "video_caixa")


def test_deitado_que_tremula_nao_e_queda(cfg):
    # Pessoa já deitada (caixa larga) cuja altura oscila: não havia ninguém em pé para cair.
    caixas = [(round(i * 0.1, 2), 0.0, 0.0, 400.0, 200.0 if i < 10 else 110.0) for i in range(30)]
    assert quedas_por_caixa(caixas, cfg.video) == []


def test_observacoes_de_caixas_por_regra(cfg):
    from movimentacao.video import CaixasDoVideo, observacoes_de_caixas

    caixas = CaixasDoVideo(_deitado_que_cai(150, 0.4), altura=1000, largura=1000, fps=10.0)
    so_descida = observacoes_de_caixas(caixas, instante("03:00"), cfg.video)
    assert [o.fonte for o in so_descida] == ["video_descida"]
    assert so_descida[0].queda and so_descida[0].posicao is P.CHAO
    assert observacoes_de_caixas(caixas, instante("03:00"), cfg.video, regras=()) == []


def test_regras_de_caixa_escalam_para_video_longo(cfg):
    # Câmera de leito contínua: 1 h a 10 fps não pode ser quadrática.
    import time

    caixas = [(i / 10, 100.0, 150.0, 400.0, 250.0) for i in range(36_000)]
    inicio = time.perf_counter()
    assert quedas_por_descida(caixas, 1000, cfg.video) == []
    assert quedas_por_caixa(caixas, cfg.video) == []
    assert time.perf_counter() - inicio < 5


def _janelas(*trechos):
    """[(kp, segundos), ...] -> quadros a cada 500 ms."""
    quadros, ms = [], 0
    for kp, segundos in trechos:
        for _ in range(int(segundos * 2)):
            quadros.append((ms, kp))
            ms += 500
    return _seq(quadros)


def test_oscilacao_curta_da_pose_nao_e_reposicionamento(cfg):
    from movimentacao.serie import marcos

    seq = _janelas((SUPINO, 20), (LATERAL_ESQ, 2), (SUPINO, 20))
    assert marcos(observacoes_de_pose(seq, instante("10:00"), cfg), cfg) == []


def test_troca_que_persiste_e_reposicionamento(cfg):
    from movimentacao.serie import marcos

    seq = _janelas((SUPINO, 20), (LATERAL_ESQ, 20))
    [m] = marcos(observacoes_de_pose(seq, instante("10:00"), cfg), cfg)
    assert m.tipo == "mudanca_posicao"
    assert m.instante == instante("10:00:20")  # o início da posição nova, não o fim da espera
