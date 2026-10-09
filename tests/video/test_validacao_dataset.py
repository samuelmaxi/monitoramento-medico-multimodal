"""Testes do validador estrutural do dataset (``video.validacao_dataset``)."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from video.anotacoes import Anotacao, escrever_label
from video.dataset import criar_estrutura
from video.validacao_dataset import validar_dataset


def _imagem(caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(caminho), np.full((16, 16, 3), 90, dtype=np.uint8))


def test_valida_dataset_ok(tmp_path: Path):
    criar_estrutura(tmp_path)
    for split in ("train", "val", "test"):
        _imagem(tmp_path / "images" / split / "a.jpg")
        escrever_label(
            tmp_path / "labels" / split / "a.txt", [Anotacao(0, 0.5, 0.5, 0.2, 0.2)]
        )
    relatorio = validar_dataset(tmp_path, classes=["person"])
    assert relatorio.ok
    assert relatorio.estatisticas["com_objetos"] == 3


def test_detecta_imagem_sem_label(tmp_path: Path):
    criar_estrutura(tmp_path)
    _imagem(tmp_path / "images" / "train" / "a.jpg")
    relatorio = validar_dataset(tmp_path, classes=["person"], splits=("train",))
    assert not relatorio.ok
    assert any("sem arquivo de label" in p.mensagem for p in relatorio.erros)


def test_detecta_classe_fora_do_intervalo_e_caixa_degenerada(tmp_path: Path):
    criar_estrutura(tmp_path)
    _imagem(tmp_path / "images" / "train" / "a.jpg")
    label = tmp_path / "labels" / "train" / "a.txt"
    label.write_text("9 0.5 0.5 0.2 0.2\n0 0.5 0.5 0.0 0.2\n", encoding="utf-8")
    relatorio = validar_dataset(tmp_path, classes=["person"], splits=("train",))
    assert not relatorio.ok
    assert sum(1 for p in relatorio.erros if "fora do intervalo" in p.mensagem) == 1


def test_negativa_vazia_nao_e_erro(tmp_path: Path):
    criar_estrutura(tmp_path)
    _imagem(tmp_path / "images" / "train" / "a.jpg")
    (tmp_path / "labels" / "train" / "a.txt").write_text("", encoding="utf-8")
    relatorio = validar_dataset(tmp_path, classes=["person"], splits=("train",))
    assert relatorio.ok
    assert relatorio.estatisticas["negativas"] == 1


def test_detecta_vazamento_de_grupo_entre_splits(tmp_path: Path):
    from video.dataset import NOME_FRAMES_DIVISAO

    criar_estrutura(tmp_path)
    linhas = [
        json.dumps({"frame_id": "vid1/f1", "chave_grupo": "vid1", "split": "train"}),
        json.dumps({"frame_id": "vid1/f2", "chave_grupo": "vid1", "split": "val"}),
        json.dumps({"frame_id": "vid2/f1", "chave_grupo": "vid2", "split": "val"}),
    ]
    (tmp_path / "metadata" / NOME_FRAMES_DIVISAO).write_text(
        "\n".join(linhas) + "\n", encoding="utf-8"
    )

    from video.dataset import verificar_vazamento

    vazamentos = verificar_vazamento(tmp_path)
    assert vazamentos == [{"grupo": "vid1", "splits": "train,val"}]


def test_validar_dataset_reporta_vazamento(tmp_path: Path):
    from video.dataset import NOME_FRAMES_DIVISAO

    criar_estrutura(tmp_path)
    for split in ("train", "val", "test"):
        _imagem(tmp_path / "images" / split / "a.jpg")
        escrever_label(
            tmp_path / "labels" / split / "a.txt", [Anotacao(0, 0.5, 0.5, 0.2, 0.2)]
        )
    linhas = [
        json.dumps({"frame_id": "vid1/f1", "chave_grupo": "vid1", "split": "train"}),
        json.dumps({"frame_id": "vid1/f2", "chave_grupo": "vid1", "split": "val"}),
    ]
    (tmp_path / "metadata" / NOME_FRAMES_DIVISAO).write_text(
        "\n".join(linhas) + "\n", encoding="utf-8"
    )
    relatorio = validar_dataset(tmp_path, classes=["person"])
    assert not relatorio.ok
    assert any("vid1" in p.mensagem and "splits" in p.mensagem for p in relatorio.erros)


def test_classe_sem_exemplos_gera_aviso(tmp_path: Path):
    criar_estrutura(tmp_path)
    _imagem(tmp_path / "images" / "train" / "a.jpg")
    escrever_label(tmp_path / "labels" / "train" / "a.txt", [Anotacao(0, 0.5, 0.5, 0.2, 0.2)])
    relatorio = validar_dataset(tmp_path, classes=["person", "cadeira"], splits=("train",))
    assert any("sem exemplos" in p.mensagem for p in relatorio.avisos)
