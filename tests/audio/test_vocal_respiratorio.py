"""US12: eventos respiratórios (tosse, respiração, sibilo, arfar) com YAMNet, score por trecho."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from audio.vocal.respiratorio import (
    N_CLASSES,
    PASSO_S,
    QUADRO_S,
    DetectorRespiratorio,
    ErroDeModelo,
    SessaoYamnetOnnx,
    carregar_detector,
    carregar_nomes_classes,
)
from contratos import Severidade

from .conftest import RAIZ
from .test_vocal_config import ErroDeConfiguracao

INDICES = {"Cough": 42, "Breathing": 36, "Wheeze": 37, "Gasp": 39, "Pant": 40}
NOMES = [f"classe_{i}" for i in range(N_CLASSES)]
for _nome, _i in INDICES.items():
    NOMES[_i] = _nome
SEGUNDOS = 10
SINAL = np.zeros(SEGUNDOS * 16000, dtype=np.float32)


def sessao_com(**por_classe: dict[int, float]):
    """Sessão falsa: ``Cough={3: 0.8, 4: 0.9}`` põe esses scores nesses quadros."""
    n_quadros = 20
    scores = np.zeros((n_quadros, N_CLASSES), dtype=np.float32)
    for classe, quadros in por_classe.items():
        for quadro, valor in quadros.items():
            scores[quadro, INDICES[classe]] = valor
    return lambda _waveform: scores


def detector(cfg_vocal, **por_classe):
    return DetectorRespiratorio(sessao_com(**por_classe), NOMES, cfg_vocal.respiratorio)


def test_quadros_seguidos_acima_do_minimo_viram_um_evento_com_score_maximo(cfg_vocal):
    eventos = detector(cfg_vocal, Cough={3: 0.8, 4: 0.9, 5: 0.6}).detectar(SINAL, 16000)

    assert len(eventos) == 1
    e = eventos[0]
    assert (e.tipo, e.classe_audioset, e.quadros) == ("tosse", "Cough", 3)
    assert e.score_max == pytest.approx(0.9)
    assert e.score_medio == pytest.approx((0.8 + 0.9 + 0.6) / 3, abs=1e-6)
    assert e.inicio_s == pytest.approx(3 * PASSO_S)
    assert e.fim_s == pytest.approx(5 * PASSO_S + QUADRO_S)


def test_score_abaixo_do_minimo_nao_gera_evento(cfg_vocal):
    minimo = cfg_vocal.respiratorio.score_minimo

    eventos = detector(cfg_vocal, Cough={3: minimo - 0.01}).detectar(SINAL, 16000)

    assert eventos == []


def test_lacuna_de_um_quadro_ainda_e_o_mesmo_evento_mas_duas_nao(cfg_vocal):
    junto = detector(cfg_vocal, Breathing={2: 0.5, 4: 0.5}).detectar(SINAL, 16000)
    separado = detector(cfg_vocal, Breathing={2: 0.5, 5: 0.5}).detectar(SINAL, 16000)

    assert len(junto) == 1
    assert junto[0].quadros == 2
    assert len(separado) == 2


def test_cada_classe_tem_tipo_e_severidade_da_config(cfg_vocal):
    eventos = detector(cfg_vocal, Wheeze={1: 0.7}, Cough={8: 0.7}).detectar(SINAL, 16000)

    por_tipo = {e.tipo: e.severidade for e in eventos}
    assert por_tipo == {"sibilo": Severidade.MEDIA, "tosse": Severidade.BAIXA}
    assert [e.tipo for e in eventos] == ["sibilo", "tosse"]  # em ordem de tempo


def test_fala_do_medico_nao_gera_evento_do_paciente(cfg_vocal):
    # o quadro 6 tem centro em 6*0,48 + 0,4875 = 3,37 s
    eventos = detector(cfg_vocal, Cough={6: 0.9}).detectar(SINAL, 16000, ignorar_s=[(3.0, 4.0)])

    assert eventos == []
    assert len(detector(cfg_vocal, Cough={6: 0.9}).detectar(SINAL, 16000)) == 1


def test_evento_no_fim_do_audio_nao_passa_da_duracao(cfg_vocal):
    curto = np.zeros(int(1.5 * 16000), dtype=np.float32)

    eventos = detector(cfg_vocal, Gasp={2: 0.9}).detectar(curto, 16000)

    assert eventos[0].fim_s == pytest.approx(1.5)


def test_acima_do_maximo_ficam_os_de_maior_score(cfg_vocal, caplog):
    cfg = replace(cfg_vocal.respiratorio, max_eventos=2, lacuna_quadros=0)
    d = DetectorRespiratorio(
        sessao_com(Cough={1: 0.4, 4: 0.9, 7: 0.5, 10: 0.95}), NOMES, cfg)

    with caplog.at_level(logging.WARNING, logger="audio.vocal"):
        eventos = d.detectar(SINAL, 16000)

    assert sorted(e.score_max for e in eventos) == pytest.approx([0.9, 0.95], abs=1e-6)
    assert any("mantidos os 2" in r.getMessage() for r in caplog.records)


def test_taxa_diferente_de_16khz_e_recusada(cfg_vocal):
    with pytest.raises(ValueError, match="16000 Hz"):
        detector(cfg_vocal).detectar(np.zeros(8000, dtype=np.float32), 8000)


def test_sinal_vazio_nao_gera_evento(cfg_vocal):
    assert detector(cfg_vocal).detectar(np.zeros(0, dtype=np.float32), 16000) == []


def test_classe_da_config_que_nao_existe_no_mapa_e_recusada(cfg_vocal):
    nomes = [n if n != "Gasp" else "outra" for n in NOMES]

    with pytest.raises(ErroDeConfiguracao, match="Gasp"):
        DetectorRespiratorio(sessao_com(), nomes, cfg_vocal.respiratorio)


def test_mapa_de_classes_com_tamanho_errado_e_recusado(cfg_vocal):
    with pytest.raises(ErroDeModelo, match="521"):
        DetectorRespiratorio(sessao_com(), NOMES[:10], cfg_vocal.respiratorio)


# --- mapa de classes e carregamento --------------------------------------------------------- #


def _csv(tmp_path: Path, linhas: list[str]) -> Path:
    caminho = tmp_path / "yamnet_class_map.csv"
    caminho.write_text("index,mid,display_name\n" + "\n".join(linhas) + "\n", encoding="utf-8")
    return caminho


def test_mapa_de_classes_e_lido_na_ordem_dos_indices(tmp_path):
    caminho = _csv(tmp_path, [f'{i},/m/{i},"{NOMES[i]}"' for i in reversed(range(N_CLASSES))])

    assert carregar_nomes_classes(caminho) == NOMES


def test_mapa_incompleto_ou_malformado_e_recusado(tmp_path):
    with pytest.raises(ErroDeModelo, match="521"):
        carregar_nomes_classes(_csv(tmp_path, ["0,/m/0,Speech"]))
    ruim = tmp_path / "ruim.csv"
    ruim.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ErroDeModelo, match="index,mid,display_name"):
        carregar_nomes_classes(ruim)
    with pytest.raises(ErroDeModelo, match="não encontrado"):
        carregar_nomes_classes(tmp_path / "nao_existe.csv")


def test_sem_o_modelo_baixado_o_detector_e_none_e_avisa(tmp_path, cfg_vocal, caplog):
    with caplog.at_level(logging.WARNING, logger="audio.vocal"):
        detector_ = carregar_detector(cfg_vocal.respiratorio, tmp_path / "yamnet.onnx")

    assert detector_ is None
    assert any("baixar_modelos_audio" in r.getMessage() for r in caplog.records)


def test_arquivo_que_nao_e_um_modelo_onnx_e_erro_claro(tmp_path):
    falso = tmp_path / "yamnet.onnx"
    falso.write_bytes(b"isto nao e um modelo")

    with pytest.raises(ErroDeModelo, match="não foi possível abrir"):
        SessaoYamnetOnnx(falso)


# --- modelo real (só roda se foi baixado por scripts/baixar_modelos_audio.py) ---------------- #

MODELO = RAIZ / "models" / "audio" / "yamnet.onnx"
requer_modelo = pytest.mark.skipif(
    not MODELO.is_file(), reason="rode scripts/baixar_modelos_audio.py para baixar o YAMNet")


@requer_modelo
def test_yamnet_real_fecha_a_conta_de_quadros_e_o_silencio_nao_gera_evento(cfg_vocal):
    d = carregar_detector(cfg_vocal.respiratorio, MODELO)
    assert d is not None

    scores = d._sessao(np.zeros(3 * 16000, dtype=np.float32))
    assert scores.shape[1] == N_CLASSES
    assert scores.shape[0] == 6  # 3 s: quadros de 0,975 s a cada 0,48 s, o último com zeros
    assert d.detectar(np.zeros(3 * 16000, dtype=np.float32), 16000) == []


@requer_modelo
def test_yamnet_real_nomes_de_classe_do_yaml_existem_no_mapa(cfg_vocal):
    nomes = carregar_nomes_classes(MODELO.with_name("yamnet_class_map.csv"))

    assert {c.classe_audioset for c in cfg_vocal.respiratorio.classes} <= set(nomes)
    assert nomes[0] == "Speech"
