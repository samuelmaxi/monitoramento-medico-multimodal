"""Fluxo integração: arquivo de vídeo → detector fake → ROI → contrato US04."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from contratos import EmissorJsonl
from contratos.testing import assert_eventos_validos
from video import Caixa, DetectorFalso, PipelineAreaCritica

from .conftest import deteccao


def test_pipeline_emite_enter_exit_validos_no_jsonl(
    config_base, emissor, video_sintetico, tmp_path
):
    detector = DetectorFalso(
        [
            [deteccao(Caixa(200, 100, 240, 180), frame_index=0, tempo_s=0.0)],
            [deteccao(Caixa(20, 30, 90, 90), frame_index=1, tempo_s=0.1)],
            [deteccao(Caixa(20, 30, 90, 90), frame_index=2, tempo_s=0.2)],
            [deteccao(Caixa(200, 100, 240, 180), frame_index=3, tempo_s=0.3)],
        ]
    )
    resultado = PipelineAreaCritica(config_base, detector=detector, emissor=emissor).executar(
        ate_quadro=4,
        detectado_em=datetime(2026, 10, 5, 19, 0, tzinfo=UTC),
    )

    assert detector.chamadas == 4
    assert resultado.resumo.quadros_lidos == 4
    assert [evento.event_type for evento in resultado.eventos] == [
        "entrada_area_critica",
        "saida_area_critica",
    ]
    assert all(evento.event_type != "sem_achados" for evento in emissor.eventos)
    assert resultado.eventos[0].timestamp == datetime(
        2026, 10, 5, 19, 0, 0, 100000, tzinfo=UTC
    )
    assert resultado.eventos[1].timestamp == datetime(
        2026, 10, 5, 19, 0, 0, 300000, tzinfo=UTC
    )
    assert emissor.eventos == resultado.eventos

    arquivo = tmp_path / "eventos.jsonl"
    arquivo.write_text("\n".join(evento.para_json() for evento in emissor.eventos), encoding="utf-8")
    assert_eventos_validos(arquivo, modalidade="video")


def test_pipeline_emite_sem_achados_quando_sem_transicoes(
    config_base, video_sintetico, tmp_path
):
    detector = DetectorFalso(
        [
            [deteccao(Caixa(200, 200, 280, 280), frame_index=i, tempo_s=i * 0.1)]
            for i in range(20)
        ]
    )
    jsonl = tmp_path / "eventos_sem_achados.jsonl"
    resultado = PipelineAreaCritica(
        config_base, detector=detector, emissor=EmissorJsonl(jsonl)
    ).executar(detectado_em=datetime(2026, 10, 5, 19, 0, tzinfo=UTC))

    assert resultado.eventos == []
    assert resultado.resumo.quadros_lidos == 20
    assert resultado.resumo.saidas == 0
    assert_eventos_validos(jsonl, modalidade="video")

    registros = [
        json.loads(linha)
        for linha in jsonl.read_text(encoding="utf-8").splitlines()
        if linha.strip()
    ]
    assert len(registros) == 1
    assert registros[0]["event_type"] == "sem_achados"
    assert registros[0]["severity"] == "info"
    assert registros[0]["score"] == 0.0
    assert registros[0]["evidence"]["features"]["quadros_lidos"] == 20
    assert registros[0]["evidence"]["features"]["entradas"] == 0
    assert registros[0]["evidence"]["features"]["saidas"] == 0
    assert registros[0]["window"]["start"] == registros[0]["timestamp"]
