"""Fluxo integração: arquivo de vídeo → detector fake → ROI → contrato US04."""

from __future__ import annotations

from datetime import UTC, datetime

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
