"""Testes de divisão, ``data.yaml`` e gate (``video.dataset``)."""

from __future__ import annotations

from pathlib import Path

from video.anotacoes import Anotacao, escrever_label
from video.dataset import (
    RegistroFrame,
    agrupar_por_origem,
    avaliar_gate,
    carregar_registros,
    gerar_data_yaml,
    materializar_divisao,
    planejar_divisao,
    verificar_aptidao,
    verificar_vazamento,
)


def _registro(frame_id: str, grupo: str, *, imagem: str = "") -> RegistroFrame:
    return RegistroFrame(
        frame_id=frame_id,
        video_id=grupo,
        chave_grupo=grupo,
        arquivo_imagem=imagem or f"frames/{grupo}/{frame_id}.jpg",
        condicao=None,
        cenario="cama",
        modalidade=None,
    )


def test_divisao_sem_vazamento_por_grupo(tmp_path: Path):
    registros = [_registro(f"f{i}", f"vid{i}") for i in range(10)]
    grupos = agrupar_por_origem(registros)
    divisao, avisos = planejar_divisao(grupos, semente=1)
    assert set(divisao) == {f"vid{i}" for i in range(10)}
    assert set(divisao.values()) <= {"train", "val", "test"}
    assert len(set(divisao.values())) == 3
    assert avisos == []


def test_agrupa_por_chave_remove_sufixo_derivado(tmp_path: Path):
    registros = [
        _registro("a1", "B_N_87"),
        _registro("b1", "B_N_87"),
    ]
    grupos = agrupar_por_origem(registros)
    assert len(grupos) == 1
    assert len(grupos[0].registros) == 2


def test_funde_grupos_com_mesmo_sha():
    registros = [_registro("a", "B_D_0003"), _registro("b", "B_D_0004")]
    grupos = agrupar_por_origem(
        registros, hashes_por_grupo={"B_D_0003": ["shaX"], "B_D_0004": ["shaX"]}
    )
    assert len(grupos) == 1


def test_materializar_nao_colide_nomes_entre_videos(tmp_path: Path):
    for vid in ("vid1", "vid2"):
        origem = tmp_path / "frames" / vid / "frame_000001.jpg"
        origem.parent.mkdir(parents=True, exist_ok=True)
        origem.write_bytes(b"frame")
    registros = [
        _registro("vid1/frame_000001", "vid1", imagem="frames/vid1/frame_000001.jpg"),
        _registro("vid2/frame_000001", "vid2", imagem="frames/vid2/frame_000001.jpg"),
    ]
    grupos = agrupar_por_origem(registros)
    divisao = {g.chave: "train" for g in grupos}
    resultado = materializar_divisao(tmp_path, registros, grupos, divisao, classes=["person"])
    imagens = sorted(p.name for p in (tmp_path / "images" / "train").glob("*.jpg"))
    assert imagens == ["vid1__frame_000001.jpg", "vid2__frame_000001.jpg"]
    assert resultado.por_split["train"] == 2


def test_materializar_e_verificar_vazamento(tmp_path: Path):
    registros = [_registro(f"f{i}", f"vid{i}") for i in range(6)]
    grupos = agrupar_por_origem(registros)
    divisao = {g.chave: ("train" if i < 4 else "val") for i, g in enumerate(grupos)}
    resultado = materializar_divisao(tmp_path, registros, grupos, divisao, classes=["person"])
    assert resultado.data_yaml.is_file()
    assert not verificar_vazamento(tmp_path)


def test_verificar_aptidao_bloqueia_sem_anotacoes(tmp_path: Path):
    avaliacao = verificar_aptidao(tmp_path, classes=["person"])
    assert avaliacao.apto is False
    assert avaliacao.motivos


def test_verificar_aptidao_aprova_com_label(tmp_path: Path):
    from video.dataset import criar_estrutura

    criar_estrutura(tmp_path)
    for split in ("train", "val", "test"):
        imagem = tmp_path / "images" / split / "a.jpg"
        imagem.write_bytes(b"fake")
        if split == "train":
            escrever_label(
                tmp_path / "labels" / split / "a.txt", [Anotacao(0, 0.5, 0.5, 0.2, 0.2)]
            )
        else:
            escrever_label(tmp_path / "labels" / split / "a.txt", [Anotacao(0, 0.4, 0.4, 0.2, 0.2)])
    gerar_data_yaml(tmp_path, classes=["person"])
    avaliacao = verificar_aptidao(tmp_path, classes=["person"])
    assert avaliacao.apto is True


def test_gate_bloqueado_sem_medicao():
    gate = avaliar_gate(mapa_50=None)
    assert gate.aprovado is False
    assert gate.medido is False


def test_gate_aprova_e_reprova():
    assert avaliar_gate(mapa_50=0.6).aprovado is True
    assert avaliar_gate(mapa_50=0.49).aprovado is False


def test_data_yaml_carrega_na_ultralytics(tmp_path: Path):
    from video.dataset import criar_estrutura

    criar_estrutura(tmp_path)
    gerar_data_yaml(tmp_path, classes=["person"], path_relativo=str(tmp_path.resolve()))
    from ultralytics.data.utils import check_det_dataset

    dados = check_det_dataset(str(tmp_path / "data.yaml"))
    assert dados["names"] == {0: "person"}
    assert dados["nc"] == 1


def test_carregar_registros(tmp_path: Path):
    manifesto = tmp_path / "m.jsonl"
    manifesto.write_text(
        '{"frame_id": "v/f1", "video_id": "v", "chave_grupo": "v", '
        '"arquivo_imagem": "frames/v/f1.jpg", "duplicado": false}\n',
        encoding="utf-8",
    )
    registros = carregar_registros(manifesto)
    assert registros[0].chave_grupo == "v"
    assert registros[0].anotavel is True
