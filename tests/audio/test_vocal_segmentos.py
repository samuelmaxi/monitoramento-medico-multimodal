"""US12: segmentos de fala do paciente (transcrição do Azure Speech ou VAD por energia)."""

from __future__ import annotations

import numpy as np
import pytest

from audio.vocal.erros import TranscricaoInvalida
from audio.vocal.segmentos import segmentos_da_transcricao, segmentos_por_vad

from .conftest import silencio, tom


def _fala(offset_ms, duracao_ms, falante, *, palavras=3):
    return {
        "texto": "uma duas tres",
        "falante": falante,
        "papel": None,
        "offset_ms": offset_ms,
        "duracao_ms": duracao_ms,
        "palavras": [{"texto": "x", "offset_ms": offset_ms, "duracao_ms": 100}] * palavras,
    }


def test_falante_unico_sem_medico_e_tratado_como_paciente_e_marcado():
    transcricao = {"falas": [_fala(0, 2000, 1), _fala(2500, 1500, 1)]}

    resultado = segmentos_da_transcricao(transcricao)

    assert resultado.papel_assumido is True
    assert [s.inicio_ms for s in resultado.segmentos] == [0, 2500]
    assert resultado.fonte == "transcricao"
    assert any("único falante" in n for n in resultado.notas)


def test_dois_falantes_sem_medico_falante_e_recusado():
    transcricao = {"falas": [_fala(0, 1000, 1), _fala(1200, 1000, 2)]}

    with pytest.raises(TranscricaoInvalida, match="medico_falante"):
        segmentos_da_transcricao(transcricao)


def test_voz_do_medico_nao_vira_segmento_do_paciente():
    transcricao = {"falas": [_fala(0, 2000, 1), _fala(2200, 1000, 2), _fala(3500, 2000, 1)]}

    resultado = segmentos_da_transcricao(transcricao, medico_falante=2)

    assert resultado.papel_assumido is False
    assert [(s.inicio_ms, s.fim_ms) for s in resultado.segmentos] == [(0, 2000), (3500, 5500)]
    assert resultado.intervalos_medico_ms == ((2200, 3200),)
    assert resultado.intervalos_medico_s == ((2.2, 3.2),)


def test_so_e_contiguo_quando_nao_ha_fala_do_medico_entre_os_segmentos():
    transcricao = {"falas": [
        _fala(0, 1000, 1), _fala(1500, 1000, 1),   # paciente, paciente: a lacuna é pausa dele
        _fala(3000, 1000, 2),                      # médico
        _fala(4500, 1000, 1),                      # paciente depois do médico: troca de turno
    ]}

    resultado = segmentos_da_transcricao(transcricao, medico_falante=2)

    assert [s.contiguo_ao_anterior for s in resultado.segmentos] == [False, True, False]
    assert [s.indice for s in resultado.segmentos] == [0, 1, 2]


def test_medico_falante_do_json_vale_quando_nao_e_informado():
    transcricao = {"medico_falante": 2, "falas": [_fala(0, 1000, 1), _fala(1200, 800, 2)]}

    resultado = segmentos_da_transcricao(transcricao)

    assert len(resultado.segmentos) == 1
    assert resultado.intervalos_medico_ms == ((1200, 2000),)


def test_argumento_tem_precedencia_sobre_o_json():
    transcricao = {"medico_falante": 1, "falas": [_fala(0, 1000, 1), _fala(1200, 800, 2)]}

    resultado = segmentos_da_transcricao(transcricao, medico_falante=2)

    assert resultado.segmentos[0].inicio_ms == 0


def test_falas_fora_de_ordem_sao_ordenadas_e_palavras_vem_da_lista_ou_do_texto():
    sem_lista = {**_fala(3000, 1000, 1), "palavras": []}
    transcricao = {"falas": [sem_lista, _fala(0, 1000, 1, palavras=5)]}

    resultado = segmentos_da_transcricao(transcricao)

    assert [s.inicio_ms for s in resultado.segmentos] == [0, 3000]
    assert [s.palavras for s in resultado.segmentos] == [5, 3]  # 3 = len("uma duas tres".split())


def test_fala_sem_falante_e_ignorada_com_nota():
    transcricao = {"falas": [_fala(0, 1000, 1), _fala(1500, 1000, None)]}

    resultado = segmentos_da_transcricao(transcricao)

    assert len(resultado.segmentos) == 1
    assert any("sem falante" in n for n in resultado.notas)


@pytest.mark.parametrize(
    ("transcricao", "trecho"),
    [
        ({}, "não tem falas"),
        ({"falas": []}, "não tem falas"),
        ({"falas": "texto"}, "não tem falas"),
        ({"falas": [_fala(0, 1000, None)]}, "falante identificado"),
        ({"falas": [_fala(0, 1000, 1), {"offset_ms": -5, "duracao_ms": 10, "falante": 1}]},
         r"falas\[1\]\.offset_ms"),
        ({"falas": [_fala(0, 1000, 1), {"offset_ms": 5, "duracao_ms": "x", "falante": 1}]},
         r"falas\[1\]\.duracao_ms"),
        ({"falas": [42]}, r"falas\[0\]"),
    ],
)
def test_transcricao_invalida_tem_mensagem_com_o_indice(transcricao, trecho):
    with pytest.raises(TranscricaoInvalida, match=trecho):
        segmentos_da_transcricao(transcricao)


def test_vad_separa_duas_falas_por_um_silencio_longo(cfg_vocal):
    sinal = np.concatenate([tom(2.0), silencio(3.0), tom(2.0)])[:, 0]

    resultado = segmentos_por_vad(sinal, 16000, cfg_vocal)

    assert resultado.fonte == "vad"
    assert resultado.papel_assumido is True
    assert len(resultado.segmentos) == 2
    primeiro, segundo = resultado.segmentos
    assert primeiro.inicio_ms == pytest.approx(0, abs=40)
    assert primeiro.fim_ms == pytest.approx(2000, abs=40)
    assert segundo.inicio_ms == pytest.approx(5000, abs=40)
    assert all(s.palavras is None for s in resultado.segmentos)
    assert [s.contiguo_ao_anterior for s in resultado.segmentos] == [False, True]


def test_vad_junta_fala_separada_por_pausa_curta(cfg_vocal):
    sinal = np.concatenate([tom(1.0), silencio(0.4), tom(1.0)])[:, 0]

    resultado = segmentos_por_vad(sinal, 16000, cfg_vocal)

    assert len(resultado.segmentos) == 1
    assert resultado.segmentos[0].fim_ms == pytest.approx(2400, abs=40)


def test_vad_sem_amostras_devolve_vazio(cfg_vocal):
    assert segmentos_por_vad(np.zeros(0, dtype=np.float32), 16000, cfg_vocal).segmentos == ()


def test_vad_nao_acha_fala_em_silencio_nem_em_ruido_de_ambiente(cfg_vocal):
    rng = np.random.default_rng(0)
    ruido = (0.0005 * rng.standard_normal(16000 * 3)).astype(np.float32)

    for sinal in (np.zeros(16000 * 3, np.float32), ruido):
        resultado = segmentos_por_vad(sinal, 16000, cfg_vocal)
        assert resultado.segmentos == ()
        assert any("sem fala audível" in n for n in resultado.notas)
