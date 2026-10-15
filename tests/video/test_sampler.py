import pytest

from app.video.sampler import compute_step, should_process


def test_30fps_com_target_5_processa_1_a_cada_6():
    assert compute_step(source_fps=30.0, target_fps=5.0) == 6


def test_target_maior_que_origem_processa_todos_os_frames():
    assert compute_step(source_fps=5.0, target_fps=30.0) == 1


def test_target_igual_a_origem_processa_todos_os_frames():
    assert compute_step(source_fps=10.0, target_fps=10.0) == 1


def test_arredondamento_quando_razao_nao_e_inteira():
    # 24 / 5 = 4,8 -> arredonda para 5 (fps efetivo 4,8)
    assert compute_step(source_fps=24.0, target_fps=5.0) == 5


@pytest.mark.parametrize("source_fps", [0.0, -1.0])
def test_fps_de_origem_invalido_gera_erro(source_fps):
    with pytest.raises(ValueError):
        compute_step(source_fps=source_fps, target_fps=5.0)


@pytest.mark.parametrize("target_fps", [0.0, -5.0])
def test_fps_alvo_invalido_gera_erro(target_fps):
    with pytest.raises(ValueError):
        compute_step(source_fps=30.0, target_fps=target_fps)


def test_should_process_seleciona_indices_multiplos_do_passo():
    selecionados = [i for i in range(13) if should_process(i, step=6)]
    assert selecionados == [0, 6, 12]
