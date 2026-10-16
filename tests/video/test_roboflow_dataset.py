from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from video.roboflow_dataset import DatasetOrigem, combinar_datasets_roboflow


def _criar_dataset(
    raiz: Path,
    classe_origem: str,
    *,
    class_id: int = 0,
    splits: tuple[str, ...] = ("train", "val", "test"),
) -> Path:
    raiz.mkdir(parents=True)
    nomes = ["background", classe_origem] if class_id == 1 else [classe_origem]
    linhas = [
        "path: .",
        "train: images/train",
        "val: images/val",
        "test: images/test",
        f"nc: {len(nomes)}",
        "names:",
        *(f"  {indice}: {nome}" for indice, nome in enumerate(nomes)),
    ]
    (raiz / "data.yaml").write_text("\n".join(linhas) + "\n", encoding="utf-8")
    for indice, split in enumerate(splits):
        imagens = raiz / "images" / split
        labels = raiz / "labels" / split
        imagens.mkdir(parents=True)
        labels.mkdir(parents=True)
        nome = f"frame_{indice}.jpg"
        cor = indice + (10 if classe_origem == "surgical_tool" else 100)
        assert cv2.imwrite(str(imagens / nome), np.full((12, 16, 3), cor, dtype=np.uint8))
        (labels / f"{Path(nome).stem}.txt").write_text(
            f"{class_id} 0.5 0.5 0.25 0.25\n", encoding="utf-8"
        )
    return raiz


def test_combina_datasets_yolo_remapeando_classes_e_preservando_splits(tmp_path: Path):
    instrumentos = _criar_dataset(tmp_path / "instruments", "surgical_tool")
    equipamentos = _criar_dataset(tmp_path / "equipment", "multiparameter_monitor")
    destino = tmp_path / "combined"

    resultado = combinar_datasets_roboflow(
        [DatasetOrigem("instruments", instrumentos), DatasetOrigem("equipment", equipamentos)],
        mapa_classes={
            "instruments": {"surgical_tool": "instrumento_cirurgico"},
            "equipment": {"multiparameter_monitor": "monitor_multiparametro"},
        },
        destino=destino,
    )

    assert resultado.imagens_por_split == {"train": 2, "val": 2, "test": 2}
    assert resultado.objetos_por_classe["val"] == {
        "instrumento_cirurgico": 1,
        "monitor_multiparametro": 1,
    }
    assert (destino / "data.yaml").is_file()
    assert (destino / "images" / "train").is_dir()
    assert (destino / "images" / "val").is_dir()
    assert (destino / "images" / "test").is_dir()
    labels = sorted((destino / "labels" / "val").glob("*.txt"))
    assert {path.read_text().split()[0] for path in labels} == {"0", "1"}


def test_rejeita_split_sem_exemplos_de_cada_classe(tmp_path: Path):
    instrumentos = _criar_dataset(
        tmp_path / "instruments", "surgical_tool", splits=("train", "val")
    )

    equipamentos = _criar_dataset(tmp_path / "equipment", "multiparameter_monitor")

    with pytest.raises(ValueError, match="split 'test' sem objetos anotados"):
        combinar_datasets_roboflow(
            [DatasetOrigem("instruments", instrumentos), DatasetOrigem("equipment", equipamentos)],
            mapa_classes={
                "instruments": {"surgical_tool": "instrumento_cirurgico"},
                "equipment": {"multiparameter_monitor": "monitor_multiparametro"},
            },
            destino=tmp_path / "combined",
        )


def test_rejeita_imagem_sem_label(tmp_path: Path):
    instrumentos = _criar_dataset(tmp_path / "instruments", "surgical_tool")
    equipamentos = _criar_dataset(tmp_path / "equipment", "multiparameter_monitor")
    (instrumentos / "labels" / "val" / "frame_1.txt").unlink()

    with pytest.raises(FileNotFoundError, match="label ausente"):
        combinar_datasets_roboflow(
            [DatasetOrigem("instruments", instrumentos), DatasetOrigem("equipment", equipamentos)],
            mapa_classes={
                "instruments": {"surgical_tool": "instrumento_cirurgico"},
                "equipment": {"multiparameter_monitor": "monitor_multiparametro"},
            },
            destino=tmp_path / "combined",
        )


def test_rejeita_classes_fonte_inexistentes(tmp_path: Path):
    instrumentos = _criar_dataset(tmp_path / "instruments", "surgical_tool")
    equipamentos = _criar_dataset(tmp_path / "equipment", "multiparameter_monitor")

    with pytest.raises(ValueError, match="não encontradas"):
        combinar_datasets_roboflow(
            [DatasetOrigem("instruments", instrumentos), DatasetOrigem("equipment", equipamentos)],
            mapa_classes={
                "instruments": {"wrong_name": "instrumento_cirurgico"},
                "equipment": {"multiparameter_monitor": "monitor_multiparametro"},
            },
            destino=tmp_path / "combined",
        )
