"""Movimentação (US17) sobre o vídeo enviado, no modo real do pipeline (US20).

O YOLO da movimentação é falso (``caixas_movimentacao`` em conftest.py): o que se testa é que a
etapa usa o vídeo do paciente, e não o cenário de demonstração.
"""

from __future__ import annotations

import json

import pytest

from pipeline import ConfigPipeline, OrquestradorPipeline

from .conftest import CAIXAS_QUEDA


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.setenv("PSEUDONYM_KEY", "chave-de-teste-com-mais-de-32-bytes-000000")
    for var in ("AZURE_SPEECH_KEY", "AZURE_SPEECH_ENDPOINT", "AZURE_LANGUAGE_KEY",
                "AZURE_LANGUAGE_ENDPOINT"):
        monkeypatch.delenv(var, raising=False)


def _executar(tmp_path, video):
    config = ConfigPipeline.from_env(
        modo="real", fator_velocidade=0.0, saida_dir=tmp_path / "saida", video_path=video
    )
    resumo = OrquestradorPipeline(config).executar()
    relatorio = json.loads(resumo.caminho_relatorio.read_text(encoding="utf-8"))
    etapa = next(e for e in relatorio["etapas"] if e["etapa"] == "movimentacao")
    eventos = [json.loads(x) for x in resumo.caminho_eventos.read_text(encoding="utf-8").splitlines()]
    return resumo, etapa, eventos


def test_queda_pela_caixa_no_video_vira_achado_real_de_queda(
    tmp_path, video_sintetico, yolo_falso, caixas_movimentacao
):
    yolo_falso([])
    caixas_movimentacao(CAIXAS_QUEDA)

    resumo, etapa, eventos = _executar(tmp_path, video_sintetico())

    assert etapa["status"] == "sucesso"
    assert etapa["detalhes"]["fonte"].startswith("vídeo enviado")
    assert etapa["detalhes"]["quedas_observadas"] >= 1
    assert "queda" in etapa["detalhes"]["anomalias"]
    quedas = [e for e in eventos if e["modality"] == "movimentacao" and e["event_type"] == "queda"]
    assert quedas and not any(e["is_synthetic"] for e in quedas)
    assert {e["patient_id"] for e in quedas} == {resumo.paciente_id}
    # Nada do cenário de demonstração (190 min em supino).
    assert "imobilidade_prolongada" not in etapa["detalhes"]["anomalias"]
    assert "queda" in {e.event_type for e in resumo.risco.eventos}  # chegou à fusão


def test_video_sem_queda_nao_inventa_observacao(tmp_path, video_sintetico, yolo_falso):
    yolo_falso([])

    _, etapa, eventos = _executar(tmp_path, video_sintetico())

    assert etapa["status"] == "sucesso"
    assert etapa["eventos_emitidos"] == 0
    assert "motivo" in etapa["detalhes"]
    assert not [e for e in eventos if e["modality"] == "movimentacao"]


def test_entrada_na_area_fora_do_leito_vira_saida_do_leito(
    tmp_path, video_sintetico, yolo_falso
):
    # lateral_leito é a ROI "fora do leito" da US17 (config/us17_movimentacao.yaml).
    yolo_falso([("entrada_area_critica", 1.0, "lateral_leito"),
                ("saida_area_critica", 6.0, "lateral_leito")])

    _, etapa, _ = _executar(tmp_path, video_sintetico())

    assert etapa["detalhes"]["observacoes_por_fonte"].get("video_areas") == 1
