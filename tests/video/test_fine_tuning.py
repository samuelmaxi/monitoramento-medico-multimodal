"""Gate de fine-tuning médico: mAP@0.5 >= 0,50 no conjunto de validação."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from video.fine_tuning import (
    classes_medicas_presentes,
    ler_configuracao_dataset,
    tem_classes_medicas,
    validar_fine_tuning,
)
from video.metricas import MetricasDeteccao


def _data_yaml(tmp_path: Path, classes: tuple[str, ...] = ("person",)) -> Path:
    caminho = tmp_path / "data.yaml"
    nomes = "\n".join(f"  {indice}: {nome}" for indice, nome in enumerate(classes))
    caminho.write_text(
        f"path: .\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n{nomes}\n",
        encoding="utf-8",
    )
    return caminho


def _metricas(
    mapa_50: float | None,
    *,
    pesos: str = "best.pt",
    classes: tuple[str, ...] = ("person",),
    mapas_classes: dict[str, float] | None = None,
) -> MetricasDeteccao:
    valores = (
        {nome: mapa_50 for nome in classes if mapa_50 is not None}
        if mapas_classes is None
        else mapas_classes
    )
    return MetricasDeteccao(
        mapa_50=mapa_50,
        mapa_50_95=None if mapa_50 is None else max(0.0, mapa_50 - 0.1),
        precisao=0.8,
        recall=0.7,
        pesos=pesos,
        versao_ultralytics="8.x",
        conjunto="val",
        classes=classes,
        mapa_50_por_classe=tuple(valores.items()),
    )


class TestConfiguracaoDataset:
    def test_le_taxonomia_e_split_val(self, tmp_path: Path):
        yaml = _data_yaml(tmp_path, ("person", "instrumento_cirurgico"))
        config = ler_configuracao_dataset(yaml)
        assert config.classes == ("person", "instrumento_cirurgico")
        assert config.split_val == "images/val"

    def test_aceita_names_inline(self, tmp_path: Path):
        yaml = tmp_path / "data.yaml"
        yaml.write_text(
            "path: .\ntrain: images/train\nval: images/val\ntest: images/test\n"
            "names: {0: person, 1: monitor_multiparametro}\n"
        )
        assert ler_configuracao_dataset(yaml).classes == ("person", "monitor_multiparametro")

    def test_rejeita_data_yaml_sem_val_ou_taxonomia(self, tmp_path: Path):
        yaml = tmp_path / "data.yaml"
        yaml.write_text("path: .\ntrain: images/train\ntest: images/test\nnames:\n  0: person\n")
        with pytest.raises(ValueError, match="sem split val"):
            ler_configuracao_dataset(yaml)


class TestClassesMedicas:
    def test_classe_coco_nao_e_medica(self):
        assert classes_medicas_presentes(("person", "scissors")) == ()
        assert tem_classes_medicas(("person", "bed")) is False

    def test_detecta_classes_medicas_preservando_ordem(self):
        classes = ("person", "instrumento_cirurgico", "monitor_multiparametro")
        assert classes_medicas_presentes(classes) == (
            "instrumento_cirurgico",
            "monitor_multiparametro",
        )
        assert tem_classes_medicas(classes) is True


class TestValidarFineTuning:
    def test_gate_medico_aprovado_no_limite(self, tmp_path: Path):
        yaml = _data_yaml(tmp_path, ("person", "instrumento_cirurgico"))
        resultado = validar_fine_tuning(
            "best.pt",
            yaml,
            avaliador=lambda pesos, data, *, split, dispositivo: _metricas(
                0.80,
                classes=("person", "instrumento_cirurgico"),
                mapas_classes={"person": 0.92, "instrumento_cirurgico": 0.50},
            ),
        )
        assert resultado.aprovado is True
        assert resultado.tem_classes_medicas is True
        assert resultado.gate.conjunto == "val"
        assert resultado.gate.mapa_50 == 0.50
        assert resultado.classes_medicas == ("instrumento_cirurgico",)

    def test_gate_medico_nao_usa_media_inflada_por_classe_coco(self, tmp_path: Path):
        yaml = _data_yaml(tmp_path, ("person", "instrumento_cirurgico"))
        resultado = validar_fine_tuning(
            "best.pt",
            yaml,
            avaliador=lambda pesos, data, *, split, dispositivo: _metricas(
                0.70,
                classes=("person", "instrumento_cirurgico"),
                mapas_classes={"person": 0.95, "instrumento_cirurgico": 0.45},
            ),
        )
        assert resultado.aprovado is False
        assert resultado.gate.mapa_50 == 0.45

    def test_gate_global_sem_classes_medicas(self, tmp_path: Path):
        yaml = _data_yaml(tmp_path)
        resultado = validar_fine_tuning(
            "best.pt",
            yaml,
            avaliador=lambda pesos, data, *, split, dispositivo: _metricas(0.51),
        )
        assert resultado.aprovado is True
        assert resultado.tem_classes_medicas is False

    def test_nao_aprova_classe_medica_sem_ap_por_classe(self, tmp_path: Path):
        yaml = _data_yaml(tmp_path, ("instrumento_cirurgico",))
        resultado = validar_fine_tuning(
            "best.pt",
            yaml,
            avaliador=lambda pesos, data, *, split, dispositivo: _metricas(
                0.99,
                classes=("instrumento_cirurgico",),
                mapas_classes={},
            ),
        )
        assert resultado.aprovado is False
        assert resultado.gate.medido is False
        assert "instrumento_cirurgico" in resultado.gate.motivo

    def test_avaliador_real_recebe_classes_do_data_yaml(self, tmp_path: Path, monkeypatch):
        import video.fine_tuning as ft

        yaml = _data_yaml(
            tmp_path, ("instrumento_cirurgico", "monitor_multiparametro")
        )
        recebido = {}

        def avaliar(pesos, data, *, split, dispositivo, classes):
            recebido.update(classes=classes, split=split, pesos=pesos)
            return _metricas(
                0.61,
                classes=tuple(classes),
                mapas_classes={
                    "instrumento_cirurgico": 0.6,
                    "monitor_multiparametro": 0.62,
                },
            )

        monkeypatch.setattr(ft, "avaliar_pesos", avaliar)
        resultado = validar_fine_tuning("best.pt", yaml)

        assert recebido == {
            "classes": ("instrumento_cirurgico", "monitor_multiparametro"),
            "split": "val",
            "pesos": "best.pt",
        }
        assert resultado.aprovado is True

    def test_split_val_e_passado_ao_avaliador(self, tmp_path: Path):
        yaml = _data_yaml(tmp_path)
        recebido: dict[str, object] = {}

        def avaliador(pesos, data, *, split, dispositivo):
            recebido.update(pesos=pesos, split=split, dispositivo=dispositivo)
            return _metricas(0.55)

        validar_fine_tuning("best.pt", yaml, dispositivo="cpu", avaliador=avaliador)
        assert recebido == {"pesos": "best.pt", "split": "val", "dispositivo": "cpu"}

    def test_rejeita_classes_que_divergem_do_data_yaml(self, tmp_path: Path):
        yaml = _data_yaml(tmp_path, ("person", "instrumento_cirurgico"))
        with pytest.raises(ValueError, match="não correspondem"):
            validar_fine_tuning("best.pt", yaml, classes=("person",))

    def test_rejeita_minimo_invalido(self, tmp_path: Path):
        yaml = _data_yaml(tmp_path)
        with pytest.raises(ValueError, match=r"entre 0\.0 e 1\.0"):
            validar_fine_tuning("best.pt", yaml, minimo=1.1)

    def test_relatorio_registra_criterio_metricas_e_classes(self, tmp_path: Path):
        yaml = _data_yaml(tmp_path, ("person", "instrumento_cirurgico"))
        resultado = validar_fine_tuning(
            "best.pt",
            yaml,
            avaliador=lambda pesos, data, *, split, dispositivo: _metricas(
                0.6,
                classes=("person", "instrumento_cirurgico"),
                mapas_classes={"person": 0.7, "instrumento_cirurgico": 0.6},
            ),
        )
        destino = resultado.salvar_json(tmp_path / "reports" / "fine_tuning_val.json")
        dados = json.loads(destino.read_text(encoding="utf-8"))
        assert dados["aprovado"] is True
        assert dados["split"] == "val"
        assert dados["metrica_avaliada"] == 0.6
        assert dados["classes_medicas"] == ["instrumento_cirurgico"]
        assert dados["metricas"]["mAP@0.5_por_classe"]["instrumento_cirurgico"] == 0.6


class TestCliValidarFinetuning:
    def test_cli_aprova_e_grava_relatorio(self, tmp_path: Path, capsys, monkeypatch):
        from scripts.dataset_us07 import main
        from video import fine_tuning

        ds = tmp_path / "ds"
        ds.mkdir()
        _data_yaml(ds, ("instrumento_cirurgico",))
        monkeypatch.setattr(
            fine_tuning,
            "avaliar_pesos",
            lambda pesos, data, *, split, dispositivo, classes=None: _metricas(
                0.66,
                pesos=pesos,
                classes=("instrumento_cirurgico",),
            ),
        )
        codigo = main(["--destino", str(ds), "validar-finetuning", "--pesos", "best.pt"])
        dados = json.loads(capsys.readouterr().out)
        assert codigo == 0
        assert dados["aprovado"] is True
        assert (ds / "reports" / "fine_tuning_val.json").is_file()

    def test_cli_reprova_gate_e_retorna_1(self, tmp_path: Path, capsys, monkeypatch):
        import video.fine_tuning as ft
        from scripts.dataset_us07 import main

        ds = tmp_path / "ds"
        ds.mkdir()
        _data_yaml(ds, ("instrumento_cirurgico",))
        monkeypatch.setattr(
            ft,
            "avaliar_pesos",
            lambda pesos, data, *, split, dispositivo, classes=None: _metricas(
                0.30,
                classes=("instrumento_cirurgico",),
            ),
        )
        codigo = main(["--destino", str(ds), "validar-finetuning", "--pesos", "best.pt"])
        capsys.readouterr()
        assert codigo == 1


class _YOLOFalso:
    def __init__(self, _modelo):
        pass

    def train(self, *, data, project, name, **argumentos):
        save_dir = Path(project) / name
        pesos = save_dir / "weights" / "best.pt"
        pesos.parent.mkdir(parents=True, exist_ok=True)
        pesos.write_text("x", encoding="utf-8")
        return SimpleNamespace(save_dir=save_dir)


class TestTreinarValidaNoVal:
    def _patch(self, monkeypatch, mapa_50: float = 0.58):
        import video.dataset as ds
        import video.fine_tuning as ft

        monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=_YOLOFalso))
        monkeypatch.setattr(
            ds,
            "verificar_aptidao",
            lambda *a, **k: SimpleNamespace(apto=True, para_dict=lambda: {"apto": True}),
        )
        monkeypatch.setattr(
            ft,
            "avaliar_pesos",
            lambda pesos, data, *, split, dispositivo, classes=None: _metricas(mapa_50),
        )

    def _preparar_dataset(self, destino: Path) -> None:
        destino.mkdir(parents=True)
        _data_yaml(destino)

    def test_treinar_valida_e_aprova(self, tmp_path: Path, capsys, monkeypatch):
        from scripts.dataset_us07 import main

        self._patch(monkeypatch)
        destino = tmp_path / "ds"
        self._preparar_dataset(destino)
        codigo = main(
            ["--destino", str(destino), "treinar", "--project", str(tmp_path / "runs")]
        )
        dados = json.loads(capsys.readouterr().out)
        assert codigo == 0
        assert dados["validacao_val"]["aprovado"] is True
        assert (destino / "reports" / "fine_tuning_val.json").is_file()

    def test_treinar_reprova_gate(self, tmp_path: Path, capsys, monkeypatch):
        from scripts.dataset_us07 import main

        self._patch(monkeypatch, mapa_50=0.40)
        destino = tmp_path / "ds"
        self._preparar_dataset(destino)
        codigo = main(
            ["--destino", str(destino), "treinar", "--project", str(tmp_path / "runs")]
        )
        capsys.readouterr()
        assert codigo == 1

    def test_treinar_sem_validar_nao_mede(self, tmp_path: Path, capsys, monkeypatch):
        from scripts.dataset_us07 import main

        self._patch(monkeypatch)
        destino = tmp_path / "ds"
        self._preparar_dataset(destino)
        codigo = main(
            [
                "--destino", str(destino), "treinar", "--sem-validar",
                "--project", str(tmp_path / "runs"),
            ]
        )
        dados = json.loads(capsys.readouterr().out)
        assert codigo == 0
        assert "validacao_val" not in dados
        assert not (destino / "reports" / "fine_tuning_val.json").is_file()
