"""Testes da infraestrutura de anotação YOLO (``video.anotacoes``)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from video.anotacoes import (
    Anotacao,
    ErroAnotacao,
    escrever_label,
    exportar_para_anotacao,
    importar_anotacoes,
    ler_label,
    manifesto_anotacoes,
    validar_arquivo_label,
)


def test_anotacao_valida_e_serializa():
    anotacao = Anotacao(0, 0.5, 0.5, 0.25, 0.4)
    linha = anotacao.para_linha()
    assert linha.split()[0] == "0"
    assert Anotacao.de_linha(linha) == anotacao


@pytest.mark.parametrize(
    "valores",
    [
        (-1, 0.5, 0.5, 0.2, 0.2),
        (0, 1.2, 0.5, 0.2, 0.2),
        (0, 0.5, 0.5, 0.0, 0.2),
        (0, 0.5, 0.5, 0.2, -0.1),
    ],
)
def test_anotacao_invalida(valores):
    with pytest.raises(ErroAnotacao):
        Anotacao(*valores)


def test_ler_label_aceita_vazio_como_negativa(tmp_path: Path):
    caminho = tmp_path / "vazio.txt"
    caminho.write_text("", encoding="utf-8")
    assert ler_label(caminho) == []


def test_escrever_e_ler_label(tmp_path: Path):
    caminho = tmp_path / "labels" / "a.txt"
    escrever_label(caminho, [Anotacao(0, 0.25, 0.25, 0.5, 0.5)])
    assert caminho.is_file()
    lidas = ler_label(caminho)
    assert len(lidas) == 1
    assert lidas[0].x_center == pytest.approx(0.25)


def test_validar_arquivo_label_detecta_erros(tmp_path: Path):
    caminho = tmp_path / "label.txt"
    caminho.write_text("5 0.5 0.5 0.2 0.2\n0 0.5 0.5 0 0.2\nabc\n", encoding="utf-8")
    problemas = validar_arquivo_label(caminho, num_classes=1)
    severidades = [p.severidade for p in problemas]
    assert severidades.count("erro") >= 2


def test_exportar_e_importar_anotacoes(tmp_path: Path):
    raiz = tmp_path / "ds"
    (raiz / "images" / "train").mkdir(parents=True)
    imagem = np.full((24, 32, 3), 128, dtype=np.uint8)
    cv2.imwrite(str(raiz / "images" / "train" / "vid__frame_000001.jpg"), imagem)
    # frame cujo nome base colide com outro vídeo: o prefixo com video_id desambigua
    cv2.imwrite(str(raiz / "images" / "train" / "outro__frame_000001.jpg"), imagem)

    destino = tmp_path / "anotavel"
    resultado = exportar_para_anotacao(
        raiz,
        destino,
        atribuicoes={"train": ["vid/frame_000001", "outro/frame_000001"]},
        classes=["person"],
    )
    assert resultado.imagens == 2
    assert (destino / "data.yaml").is_file()
    assert (destino / "pendentes_anotacao.csv").is_file()
    assert (destino / "labels" / "train" / "vid__frame_000001.txt").is_file()
    assert (destino / "labels" / "train" / "outro__frame_000001.txt").is_file()

    escrever_label(
        destino / "labels" / "train" / "vid__frame_000001.txt",
        [Anotacao(0, 0.5, 0.5, 0.4, 0.4)],
    )
    importados = importar_anotacoes(destino / "labels", raiz / "labels" / "train")
    assert "vid__frame_000001.txt" in importados


def test_manifesto_anotacoes_conta_pendentes(tmp_path: Path):
    raiz = tmp_path / "ds"
    (raiz / "images" / "train").mkdir(parents=True)
    (raiz / "labels" / "train").mkdir(parents=True)
    imagem = np.zeros((10, 10, 3), dtype=np.uint8)
    cv2.imwrite(str(raiz / "images" / "train" / "a.jpg"), imagem)
    cv2.imwrite(str(raiz / "images" / "train" / "b.jpg"), imagem)
    escrever_label(raiz / "labels" / "train" / "a.txt", [Anotacao(0, 0.5, 0.5, 0.2, 0.2)])
    manifesto = manifesto_anotacoes(raiz, splits=("train",))
    assert manifesto["anotadas_com_objetos"] == 1
    assert manifesto["pendentes"] == 1
