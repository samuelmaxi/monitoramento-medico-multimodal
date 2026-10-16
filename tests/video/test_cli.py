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


def redirecionar_saida(monkeypatch, tmp_path) -> Path:
    """Isola JSONLs e relatório do pipeline em ``tmp_path`` (longe de saida/)."""
    saida = tmp_path / "saida" / "video"
    saida.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("scripts.rodar_us07_video._dir_saida", lambda _config: saida)
    monkeypatch.setattr(
        "scripts.rodar_us07_video._caminho_jsonl_config",
        lambda _config: saida / "eventos_us07.jsonl",
    )
    return saida


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


def emitir_sem_achados(pipeline):
    """Simula o pipeline real: sem transições, grava um registro sem_achados."""
    from contratos import EmissorJsonl
    from scripts import rodar_us07_video as _cli
    from video import ResumoExecucao, TradutorEventos

    tradutor = TradutorEventos(pipeline.config)
    contexto = tradutor.contexto_de(descricao_modelo="yolov8n@teste")
    resumo = ResumoExecucao(
        quadros_lidos=3,
        deteccoes_total=0,
        transicoes=0,
        eventos=0,
        entradas=0,
        saidas=0,
        fps=10.0,
        resolucao=(320, 240),
        classes_detectadas={},
        duracao_s=0.1,
    )
    destino = pipeline.emissor
    if destino is None:
        caminho = _cli._caminho_jsonl_config(pipeline.config)
        destino = EmissorJsonl(caminho) if caminho is not None else None
    if destino is None:
        return
    tradutor.emitir_sem_achados(destino, resumo=resumo, contexto=contexto)


def test_cli_processa_video_configurado(monkeypatch, capsys, tmp_path):
    chamadas.clear()
    redirecionar_saida(monkeypatch, tmp_path)
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


def test_cli_video_unico_sobrescreve_fonte(monkeypatch, tmp_path):
    chamadas.clear()
    saida = redirecionar_saida(monkeypatch, tmp_path)

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
    assert emissor.caminho == (saida / "eventos_B_D_0001.jsonl").resolve()


def test_cli_lote_gera_um_jsonl_por_video(monkeypatch, tmp_path):
    chamadas.clear()
    saida = redirecionar_saida(monkeypatch, tmp_path)
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
        (saida / "eventos_a.jsonl").resolve(),
        (saida / "eventos_b.jsonl").resolve(),
    ]
    for nome in ("eventos_a.jsonl", "eventos_b.jsonl"):
        caminho = saida / nome
        assert caminho.exists()


def test_cli_lote_pula_falha_e_termina_com_erro(monkeypatch, tmp_path):
    chamadas.clear()
    redirecionar_saida(monkeypatch, tmp_path)
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


def test_cli_video_sem_eventos_grava_registro_sem_achados(monkeypatch, tmp_path):
    chamadas.clear()
    saida = redirecionar_saida(monkeypatch, tmp_path)
    (tmp_path / "vazio.mp4").touch()

    class PipelineFalso:
        __init__ = registrar

        def executar(self, *, ate_quadro, escritor_video):
            emitir_sem_achados(self)
            return fake_resultado(3, eventos=0)

    instalar_fakes(monkeypatch, PipelineFalso)
    assert main([CONFIG, "--videos-dir", str(tmp_path), "--max-frames", "3"]) == 0
    registros = (saida / "eventos_vazio.jsonl").read_text(encoding="utf-8")
    assert '"event_type":"sem_achados"' in registros
    assert '"severity":"info"' in registros


def test_cli_grava_relatorio_em_arquivo(monkeypatch, tmp_path):
    chamadas.clear()
    redirecionar_saida(monkeypatch, tmp_path)
    (tmp_path / "c.mp4").touch()

    class PipelineFalso:
        __init__ = registrar

        def executar(self, *, ate_quadro, escritor_video):
            return fake_resultado(4)

    instalar_fakes(monkeypatch, PipelineFalso)
    alvo = tmp_path / "relatorio.json"
    assert main(
        [CONFIG, "--videos-dir", str(tmp_path), "--saida-relatorio", str(alvo)]
    ) == 0
    dados = json.loads(alvo.read_text(encoding="utf-8"))
    assert dados["videos_processados"] == 1
    assert dados["videos_com_falha"] == 0
    assert "resumos" in dados
    avaliacao = dados["avaliacao"]
    assert avaliacao["metodo"] == "baseline_coco"
    assert avaliacao["metricas"]["mAP@0.5:0.95"] == 0.373
    assert avaliacao["quantitativa_em_ground_truth"] is None


def test_cli_config_sem_eventos_grava_registro_sem_achados(monkeypatch, tmp_path, capsys):
    chamadas.clear()
    saida = redirecionar_saida(monkeypatch, tmp_path)

    class PipelineFalso:
        __init__ = registrar

        def executar(self, *, ate_quadro, escritor_video):
            emitir_sem_achados(self)
            return fake_resultado(3, eventos=0)

    instalar_fakes(monkeypatch, PipelineFalso)
    assert main([CONFIG, "--max-frames", "3"]) == 0
    registros = (saida / "eventos_us07.jsonl").read_text(encoding="utf-8")
    assert '"event_type":"sem_achados"' in registros
    assert '"evidence"' in registros


def test_cli_config_recria_jsonl_sem_acumular(monkeypatch, tmp_path, capsys):
    chamadas.clear()
    saida = redirecionar_saida(monkeypatch, tmp_path)
    caminho = saida / "eventos_us07.jsonl"
    caminho.write_text("linha-antiga\n", encoding="utf-8")

    class PipelineFalso:
        __init__ = registrar

        def executar(self, *, ate_quadro, escritor_video):
            return fake_resultado(3)

    instalar_fakes(monkeypatch, PipelineFalso)
    assert main([CONFIG, "--max-frames", "3"]) == 0
    assert caminho.read_text(encoding="utf-8") == ""


def test_cli_falha_nao_cria_jsonl_mas_fica_no_relatorio(monkeypatch, tmp_path):
    chamadas.clear()
    saida = redirecionar_saida(monkeypatch, tmp_path)
    (tmp_path / "ruim.mp4").touch()

    class PipelineComFalha:
        __init__ = registrar

        def executar(self, *, ate_quadro, escritor_video):
            raise RuntimeError("vídeo corrompido")

    instalar_fakes(monkeypatch, PipelineComFalha)
    alvo = tmp_path / "relatorio.json"
    assert main(
        [CONFIG, "--videos-dir", str(tmp_path), "--saida-relatorio", str(alvo)]
    ) == 1
    caminho = saida / "eventos_ruim.jsonl"
    assert not caminho.exists()
    dados = json.loads(alvo.read_text(encoding="utf-8"))
    assert dados["videos_com_falha"] == 1
    assert dados["falhas"][0]["erro"] == "vídeo corrompido"


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
