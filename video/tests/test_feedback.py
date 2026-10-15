"""Testes do feedback em linguagem natural (regras sobre os ângulos por quadro)."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from video.pose.angles import ANGLE_COLUMNS
from video.pose.config import load_config
from video.pose.feedback import AVISO, build_feedback

CFG = load_config(Path(__file__).resolve().parents[1] / "pose" / "config.yaml").feedback


def _angulos(n: int = 50, **colunas) -> pd.DataFrame:
    """``n`` quadros a 10 fps em postura neutra; ``colunas`` sobrescreve valores por quadro."""
    neutro = {c: 170.0 for c in ANGLE_COLUMNS}
    neutro.update({c: 2.0 for c in ANGLE_COLUMNS if c.startswith(("assimetria", "inclinacao"))})
    df = pd.DataFrame({"frame_idx": range(0, 3 * n, 3), "timestamp_ms": range(0, 100 * n, 100)})
    for coluna, valor in neutro.items():
        df[coluna] = colunas.get(coluna, valor)
    return df


def _feedback(angulos: pd.DataFrame, pct: float = 100.0, frames: int | None = None):
    return build_feedback(
        angulos,
        frames_com_esqueleto=len(angulos) if frames is None else frames,
        pct_frames_com_esqueleto=pct,
        fps_processado=10.0,
        duracao_s=len(angulos) / 10,
        cfg=CFG,
    )


def test_postura_neutra_so_tem_pontos_positivos():
    fb = _feedback(_angulos())
    assert fb.pontos_negativos == [] and fb.pontos_de_alerta == []
    assert len(fb.pontos_positivos) == 4
    assert fb.resumo.startswith("Nenhum desvio postural encontrado em 5 s de vídeo.")
    assert fb.aviso == AVISO


def test_tronco_inclinado_e_ponto_negativo_com_duracao_e_pico():
    tronco = [2.0] * 10 + [40.0] * 19 + [45.0] + [2.0] * 20
    fb = _feedback(_angulos(inclinacao_tronco=tronco))
    assert fb.pontos_de_alerta == []
    (frase,) = fb.pontos_negativos
    assert "mais de 30°" in frase and "por 2,0 s" in frase and "pico de 45° aos 3 s" in frase
    assert fb.resumo.startswith("Em 5 s de vídeo a análise encontrou 1 ponto negativo.")
    assert frase in fb.resumo


def test_tronco_muito_inclinado_e_alerta_e_nao_repete_como_negativo():
    fb = _feedback(_angulos(inclinacao_tronco=[2.0] * 10 + [75.0] * 15 + [2.0] * 25))
    (frase,) = fb.pontos_de_alerta
    assert "mais de 60°" in frase and "por 1,5 s" in frase
    assert fb.pontos_negativos == []
    assert "1 ponto de alerta" in fb.resumo


def test_desvio_curto_e_ignorado():
    fb = _feedback(_angulos(inclinacao_tronco=[2.0] * 10 + [75.0] * 5 + [2.0] * 35))
    assert fb.pontos_negativos == [] and fb.pontos_de_alerta == []


def test_quadro_sem_medida_interrompe_o_desvio():
    # 0,8 s + lacuna + 0,8 s: nenhum trecho contínuo chega a 1 s.
    tronco = [2.0] * 10 + [40.0] * 8 + [math.nan] + [40.0] * 8 + [2.0] * 23
    fb = _feedback(_angulos(inclinacao_tronco=tronco))
    assert fb.pontos_negativos == []


def test_assimetria_e_flexao_profunda():
    fb = _feedback(_angulos(assimetria_joelho=[35.0] * 50, joelho_direito=[30.0] * 50))
    assert len(fb.pontos_negativos) == 2
    assert "entre os joelhos esquerdo e direito passou de 20°" in fb.pontos_negativos[0]
    assert "joelho direito ficou flexionado além de 135°" in fb.pontos_negativos[1]
    assert "pico de 150°" in fb.pontos_negativos[1]
    assert "2 pontos negativos" in fb.resumo
    assert not any("faixa de flexão" in p for p in fb.pontos_positivos)


def test_deteccao_baixa_e_articulacao_pouco_visivel_sao_alertas():
    ombro = [math.nan] * 40 + [20.0] * 10
    fb = _feedback(_angulos(ombro_direito=ombro), pct=35.0)
    assert len(fb.pontos_de_alerta) == 2
    assert "35,0% dos quadros" in fb.pontos_de_alerta[0]
    assert "ombro direito" in fb.pontos_de_alerta[1]
    assert "2 pontos de alerta" in fb.resumo


def test_deteccao_intermediaria_e_ponto_negativo():
    fb = _feedback(_angulos(), pct=65.0)
    assert any("65,0% dos quadros" in p for p in fb.pontos_negativos)


def test_ninguem_detectado():
    vazio = _angulos(**{c: math.nan for c in ANGLE_COLUMNS})
    fb = _feedback(vazio, pct=0.0, frames=0)
    assert fb.pontos_positivos == [] and fb.pontos_negativos == []
    assert fb.resumo == fb.pontos_de_alerta[0]
    assert "Nenhuma pessoa foi detectada" in fb.resumo


@pytest.mark.parametrize("coluna", ["inclinacao_tronco", "assimetria_ombro"])
def test_medida_ausente_nao_vira_ponto_positivo(coluna):
    fb = _feedback(_angulos(**{coluna: math.nan}))
    chave = "tronco" if coluna == "inclinacao_tronco" else "ombros"
    assert not any(chave in p for p in fb.pontos_positivos)
