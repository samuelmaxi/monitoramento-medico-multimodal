"""Testes do executor CLI do pipeline US07 (vídeo único, config e lote)."""

from __future__ import annotations

import builtins
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import video
from scripts.rodar_us07_video import main

CONFIG = "config/exemplo_us07.json"

chamadas: dict = {}


def resumo_para(quadros=3, eventos=1):
    return {"quadros_lidos": quadros, "eventos": eventos, "entradas": 1, "saidas": 0}


def fake_resultado(quadros=3, eventos=1):
    dados = resumo_para(quadros, eventos)
    return SimpleNamespace(resumo=SimpleNamespace(para_dict=lambda: dados))


class DetectorFalso:
    instancias = 0
    reinicios = 0

    def __init__(self, config):
        DetectorFalso.instancias += 1
        self.config = config

    def reiniciar_rastreamento(self):
        DetectorFalso.reinicios += 1


def instalar_fakes(monkeypatch, pipeline, detector=None):
    DetectorFalso.instancias = 0
    DetectorFalso.reinicios = 0
    monkeypatch.setattr(video, "DetectorYolo", detector or DetectorFalso)
    monkeypatch.setattr(video, "PipelineAreaCritica", pipeline)
    return DetectorFalso


def registrar(self, config, *, detector=None, emissor=None):
    chamadas["init"] = (config, detector, emissor)
    chamadas.setdefault("emissores", []).append(emissor)
    self.config = config
    self.detector = detector
    self.emissor = emissor


def test_cli_processa_video_configurado(monkeypatch, capsys):
    chamadas.clear()
    resultado = fake_resultado()

    class PipelineFalso:
        __init__ = registrar

        def executar(self, *, ate_quadro, escritor_video):
            chamadas["execucao"] = (ate_quadro, escritor_video)
            return resultado

    instalar_fakes(monkeypatch, PipelineFalso)
    assert main([CONFIG, "--max-frames", "3"]) == 0
    _, detector, emissor = chamadas["init"]
    assert chamadas["execucao"] == (3, None)
    assert emissor is None
    assert isinstance(detector, DetectorFalso)
    assert '"quadros_lidos": 3' in capsys.readouterr().out


def test_cli_video_unico_sobrescreve_fonte(monkeypatch):
    chamadas.clear()

    class PipelineFalso:
        __init__ = registrar

        def executar(self, *, ate_quadro, escritor_video):
            return fake_resultado(5)

    instalar_fakes(monkeypatch, PipelineFalso)
    alvo = Path("conteudos/videos/B_D_0001.mp4")
    assert main([CONFIG, "--video", str(alvo)]) == 0
    config, _, emissor = chamadas["init"]
    assert config.fonte.video == alvo.resolve()
    assert config.fonte.id == "B_D_0001"
    assert emissor.caminho == Path("saida/video/eventos_B_D_0001.jsonl").resolve()


def test_cli_lote_gera_um_jsonl_por_video(monkeypatch, tmp_path):
    chamadas.clear()
    (a, b) = (tmp_path / "a.mp4", tmp_path / "b.mp4")
    a.touch()
    b.touch()

    class PipelineFalso:
        __init__ = registrar

        def executar(self, *, ate_quadro, escritor_video):
            chamadas.setdefault("configs", []).append(self.config)
            return fake_resultado(4)

    instalar_fakes(monkeypatch, PipelineFalso)
    assert main([CONFIG, "--videos-dir", str(tmp_path), "--max-frames", "4"]) == 0

    configs = chamadas["configs"]
    assert [c.fonte.video for c in configs] == [a.resolve(), b.resolve()]
    assert [c.fonte.id for c in configs] == ["a", "b"]
    assert DetectorFalso.instancias == 1
    assert DetectorFalso.reinicios == 1
    assert [e.caminho for e in chamadas["emissores"]] == [
        Path("saida/video/eventos_a.jsonl").resolve(),
        Path("saida/video/eventos_b.jsonl").resolve(),
    ]
    assert not (Path("saida/video/eventos_a.jsonl")).exists()
    assert not (Path("saida/video/eventos_b.jsonl")).exists()


def test_cli_lote_pula_falha_e_termina_com_erro(monkeypatch, tmp_path):
    chamadas.clear()
    (tmp_path / "ok.mp4").touch()
    (tmp_path / "ruim.mp4").touch()

    class PipelineComFalha:
        __init__ = registrar

        def executar(self, *, ate_quadro, escritor_video):
            if "ruim" in self.config.fonte.video.name:
                raise RuntimeError("vídeo corrompido")
            return fake_resultado(2)

    instalar_fakes(monkeypatch, PipelineComFalha)
    saida = []
    original = builtins.print

    def capturar(*args, **kwargs):
        saida.append(args[0])
        original(*args, **kwargs)

    monkeypatch.setattr(builtins, "print", capturar)
    assert main([CONFIG, "--videos-dir", str(tmp_path)]) == 1
    relatorio = json.loads(saida[-1])
    assert relatorio["videos_com_falha"] == 1
    assert relatorio["videos_processados"] == 1
    assert relatorio["falhas"][0]["erro"] == "vídeo corrompido"


def test_cli_rejeita_video_inexistente(monkeypatch):
    chamadas.clear()

    class PipelineFalso:
        __init__ = registrar

        def executar(self, **kwargs):
            return fake_resultado()

    instalar_fakes(monkeypatch, PipelineFalso)
    with pytest.raises(SystemExit) as erro:
        main([CONFIG, "--video", "nao_existe.mp4"])
    assert erro.value.code == 2


def test_cli_rejeita_pasta_vazia(monkeypatch, tmp_path):
    chamadas.clear()

    class PipelineFalso:
        __init__ = registrar

        def executar(self, **kwargs):
            return fake_resultado()

    instalar_fakes(monkeypatch, PipelineFalso)
    with pytest.raises(SystemExit) as erro:
        main([CONFIG, "--videos-dir", str(tmp_path)])
    assert erro.value.code == 2


def test_cli_rejeita_video_e_pasta_juntos(monkeypatch):
    chamadas.clear()

    class PipelineFalso:
        __init__ = registrar

    instalar_fakes(monkeypatch, PipelineFalso)
    with pytest.raises(SystemExit) as erro:
        main([CONFIG, "--video", "a.mp4", "--videos-dir", "b"])
    assert erro.value.code == 2


def test_cli_config_sem_arquivo_falha_com_erro_de_configuração():
    from video import ErroDeConfiguracao

    with pytest.raises(ErroDeConfiguracao, match="não encontrada"):
        main(["config/nao_existe.json"])
