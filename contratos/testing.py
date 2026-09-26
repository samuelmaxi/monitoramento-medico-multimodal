"""Utilitários para os testes dos outros módulos.

Exemplo em ``tests/video/test_saida.py``::

    from contratos.testing import assert_eventos_validos

    def test_video_emite_no_contrato(tmp_path):
        rodar_pipeline_video(saida=tmp_path / "eventos.jsonl")
        eventos = assert_eventos_validos(tmp_path / "eventos.jsonl", modalidade="video")
        assert any(e.event_type == "postura_fora_da_faixa" for e in eventos)
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .catalogo import Modalidade
from .evento import EventoAchado
from .validar import validar_arquivo


def assert_eventos_validos(
    origem: str | Path | Iterable[EventoAchado | dict],
    *,
    modalidade: Modalidade | str | None = None,
    minimo: int = 1,
) -> list[EventoAchado]:
    """Falha o teste se houver evento fora do contrato ou da modalidade esperada."""
    if isinstance(origem, (str, Path)):
        eventos, erros = validar_arquivo(origem)
        assert not erros, "Eventos fora do contrato:\n" + "\n".join(map(str, erros))
    else:
        eventos = [e if isinstance(e, EventoAchado) else EventoAchado.model_validate(e) for e in origem]

    assert len(eventos) >= minimo, f"esperava ao menos {minimo} evento(s), recebi {len(eventos)}"

    if modalidade is not None:
        esperada = Modalidade(modalidade)
        fora = [str(e.event_id) for e in eventos if e.modality is not esperada]
        assert not fora, f"eventos com modalidade diferente de '{esperada.value}': {fora}"
    return eventos
