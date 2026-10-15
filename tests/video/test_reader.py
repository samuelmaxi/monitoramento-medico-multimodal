import pytest

from app.video.errors import CorruptedVideoError, InvalidVideoFormatError
from app.video.reader import VideoReader


def test_le_todos_os_frames_com_indices_sequenciais(make_video):
    path = make_video(frames=10, fps=30.0)

    with VideoReader(path) as reader:
        frames = list(reader.frames())

    assert [f.frame_index for f in frames] == list(range(10))
    assert reader.frames_read == 10
    assert reader.partial is False


def test_timestamps_em_ms_crescentes_e_coerentes_com_o_fps(make_video):
    path = make_video(frames=31, fps=30.0)

    with VideoReader(path) as reader:
        frames = list(reader.frames())

    timestamps = [f.timestamp_ms for f in frames]
    assert timestamps[0] == 0
    assert timestamps == sorted(timestamps)
    # 30 fps -> ~33,33 ms por frame; o frame 30 está em 1000 ms.
    assert timestamps[30] == 1000
    assert timestamps[1] == 33


def test_frames_expõem_imagem_bgr(make_video):
    path = make_video(frames=3, size=(64, 48))

    with VideoReader(path) as reader:
        first = next(reader.frames())

    assert first.image.shape == (48, 64, 3)


def test_fps_de_origem_fica_disponivel_apos_abrir(make_video):
    path = make_video(frames=5, fps=25.0)

    with VideoReader(path) as reader:
        assert reader.source_fps == pytest.approx(25.0, rel=0.01)


def test_avi_é_aceito(make_video):
    path = make_video(name="sintetico.avi", frames=5, codec="MJPG")

    with VideoReader(path) as reader:
        assert len(list(reader.frames())) == 5


def test_extensao_nao_suportada_gera_erro(tmp_path):
    path = tmp_path / "exame.txt"
    path.write_bytes(b"nao sou video")

    with pytest.raises(InvalidVideoFormatError):
        with VideoReader(path):
            pass


def test_arquivo_inexistente_gera_erro(tmp_path):
    with pytest.raises(CorruptedVideoError):
        with VideoReader(tmp_path / "nao_existe.mp4"):
            pass


def test_arquivo_que_nao_é_video_com_extensao_mp4_gera_erro(tmp_path):
    path = tmp_path / "falso.mp4"
    path.write_bytes(b"isto nao e um mp4 valido" * 50)

    with pytest.raises((CorruptedVideoError, InvalidVideoFormatError)):
        with VideoReader(path):
            pass


def test_iterar_fora_do_with_gera_erro(make_video):
    reader = VideoReader(make_video(frames=2))

    with pytest.raises(RuntimeError):
        next(reader.frames())
