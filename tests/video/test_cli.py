"""Testes do executor CLI do pipeline US07."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import video
from scripts.rodar_us07_video import main


def test_cli_processa_video_configurado(monkeypatch, capsys):
    resumo = {"quadros_lidos": 3, "eventos": 0}
    resultado = SimpleNamespace(resumo=SimpleNamespace(para_dict=lambda: resumo))
    chamadas = {}

    class PipelineFalso:
        def __init__(self, config):
            chamadas["config"] = config

        def executar(self, *, ate_quadro, escritor_video):
            chamadas["execucao"] = (ate_quadro, escritor_video)
            return resultado

    monkeypatch.setattr(video, "PipelineAreaCritica", PipelineFalso)
    config = Path("config/exemplo_us07.json")
    assert main([str(config), "--max-frames", "3"]) == 0
    assert chamadas["execucao"] == (3, None)
    assert '"quadros_lidos": 3' in capsys.readouterr().out
