from pathlib import Path

import cv2
import numpy as np
import pytest

from app.video.anonymizer import FaceAnonymizer, _blur_kernel_for, _iou, _pad_box


def _fixed_faces(faces):
    """Detector falso que devolve sempre as mesmas caixas (testa só o blur)."""
    return lambda image: list(faces)


def _sequence_faces(responses):
    """Detector falso que devolve uma lista de caixas por chamada (simula rosto que some)."""
    responses = list(responses)

    def find(image):
        return responses.pop(0) if responses else []

    return find


def _textured_frame(height=120, width=160):
    rng = np.random.default_rng(seed=0)
    return rng.integers(0, 255, size=(height, width, 3), dtype=np.uint8)


def test_regiao_do_rosto_muda_e_resto_do_frame_nao():
    face = (40, 30, 60, 60)
    anonymizer = FaceAnonymizer(padding=0.0, find_faces=_fixed_faces([face]))
    frame = _textured_frame()

    output = anonymizer.apply(frame)

    x, y, w, h = face
    assert not np.array_equal(output[y : y + h, x : x + w], frame[y : y + h, x : x + w])
    mask = np.ones(frame.shape[:2], dtype=bool)
    mask[y : y + h, x : x + w] = False
    assert np.array_equal(output[mask], frame[mask])


def test_entrada_nao_é_modificada():
    anonymizer = FaceAnonymizer(find_faces=_fixed_faces([(10, 10, 40, 40)]))
    frame = _textured_frame()
    original = frame.copy()

    anonymizer.apply(frame)

    assert np.array_equal(frame, original)


def test_sem_rostos_retorna_copia_identica():
    anonymizer = FaceAnonymizer(find_faces=_fixed_faces([]))
    frame = _textured_frame()

    output = anonymizer.apply(frame)

    assert np.array_equal(output, frame)
    assert output is not frame


def test_padding_expande_a_caixa_dentro_do_frame():
    assert _pad_box(40, 30, 60, 60, 0.15, (120, 160)) == (31, 21, 78, 78)
    assert _pad_box(0, 0, 60, 60, 0.5, (120, 160)) == (0, 0, 90, 90)


def test_caixa_fora_do_frame_nao_quebra_o_blur():
    anonymizer = FaceAnonymizer(find_faces=_fixed_faces([(-200, -200, 50, 50)]))
    frame = _textured_frame()

    output = anonymizer.apply(frame)

    assert np.array_equal(output, frame)


def test_rosto_que_some_continua_borrado_pela_memoria():
    face = (40, 30, 60, 60)
    anonymizer = FaceAnonymizer(
        padding=0.0,
        memory_frames=2,
        find_faces=_sequence_faces([[face], [], []]),
    )
    frame = _textured_frame()
    x, y, w, h = face

    for _ in range(3):
        output = anonymizer.apply(frame)
        assert not np.array_equal(output[y : y + h, x : x + w], frame[y : y + h, x : x + w])


def test_memoria_expira_apos_o_limite():
    face = (40, 30, 60, 60)
    anonymizer = FaceAnonymizer(
        padding=0.0,
        memory_frames=2,
        find_faces=_sequence_faces([[face], [], [], []]),
    )
    frame = _textured_frame()
    x, y, w, h = face

    for _ in range(3):
        anonymizer.apply(frame)
    output = anonymizer.apply(frame)

    assert np.array_equal(output[y : y + h, x : x + w], frame[y : y + h, x : x + w])


def test_iou_de_caixas_identicas_e_disjuntas():
    assert _iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert _iou((0, 0, 10, 10), (50, 50, 10, 10)) == 0.0


def test_yunet_detecta_rosto_em_frame_real_do_samurai():
    """Smoke do detector real: o modelo carrega e acha pelo menos um rosto no vídeo de exemplo."""
    sample = Path("conteudos/videos/Samurai_Desembanha_Espada_Rapidamente.mp4")
    if not sample.is_file():
        pytest.skip("Vídeo de exemplo não encontrado em conteudos/.")

    cap = cv2.VideoCapture(str(sample))
    ok, frame = cap.read()
    cap.release()
    assert ok

    faces = FaceAnonymizer()._find_faces(frame)

    assert len(faces) >= 1


def test_kernel_do_blur_é_impar_e_proporcional_ao_rosto():
    assert _blur_kernel_for(30, 30) % 2 == 1
    assert _blur_kernel_for(300, 200) > _blur_kernel_for(30, 30)


@pytest.mark.parametrize("method", ["fill", "pixelate", "blur"])
def test_cada_metodo_altera_a_regiao_do_rosto(method):
    face = (40, 30, 60, 60)
    anonymizer = FaceAnonymizer(method=method, padding=0.0, find_faces=_fixed_faces([face]))
    frame = _textured_frame()

    output = anonymizer.apply(frame)

    x, y, w, h = face
    assert not np.array_equal(output[y : y + h, x : x + w], frame[y : y + h, x : x + w])
    mask = np.ones(frame.shape[:2], dtype=bool)
    mask[y : y + h, x : x + w] = False
    assert np.array_equal(output[mask], frame[mask])


def test_preenchimento_é_o_metodo_padrao():
    face = (40, 30, 60, 60)
    anonymizer = FaceAnonymizer(padding=0.0, find_faces=_fixed_faces([face]))
    frame = _textured_frame()

    output = anonymizer.apply(frame)

    x, y, w, h = face
    assert np.all(output[y : y + h, x : x + w] == 0)


def test_metodo_invalido_gera_erro():
    with pytest.raises(ValueError):
        FaceAnonymizer(method="inexistente", find_faces=_fixed_faces([]))


def test_pixelizacao_forma_blocos_uniformes():
    face = (40, 30, 64, 64)
    anonymizer = FaceAnonymizer(method="pixelate", padding=0.0, find_faces=_fixed_faces([face]))
    frame = _textured_frame()

    output = anonymizer.apply(frame)

    x, y, w, h = face
    region = output[y : y + h, x : x + w]
    # Cada bloco de 8 px é uniforme: os pixels vizinhos dentro do bloco são iguais.
    assert np.array_equal(region[0:8, 0:8], np.broadcast_to(region[0, 0], (8, 8, 3)))


def test_mosaico_usa_apenas_preto_e_branco():
    face = (40, 30, 64, 64)
    anonymizer = FaceAnonymizer(method="mosaic", padding=0.0, find_faces=_fixed_faces([face]))
    frame = _textured_frame()

    output = anonymizer.apply(frame)

    x, y, w, h = face
    region = output[y : y + h, x : x + w]
    assert set(np.unique(region).tolist()) <= {0, 255}
    assert np.array_equal(region[:, :, 0], region[:, :, 1])
    assert np.array_equal(region[:, :, 1], region[:, :, 2])
