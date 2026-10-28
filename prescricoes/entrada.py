"""Prescrição a partir de campos crus (API, casos injetados, testes).

É o mesmo caminho da leitura do MIMIC (``mimic.prescricoes_de_dataframe``):
dose em texto, unidade, via e frequência como a farmácia escreveu, e a
normalização acontece aqui.
"""

from __future__ import annotations

from datetime import datetime

from .modelos import Prescricao
from .normalizacao import grupo_de_via, interpretar_dose, tipo_de_frequencia
from .principios import principios_ativos


def criar_prescricao(
    *,
    ordem_id: str,
    paciente: str,
    internacao: str | None,
    medicamento: str,
    dose: str | float | int | None,
    unidade: str | None,
    via: str,
    frequencia: str | None,
    inicio: datetime,
    fim: datetime | None,
    doses_24h: float | None = None,
    apresentacao: str = "",
    status: str | None = None,
    escala_movel: bool = False,
    sintetica: bool = False,
) -> Prescricao:
    texto_dose = "" if dose is None else f"{dose:g}" if isinstance(dose, (int, float)) else str(dose).strip()
    if fim is not None and fim < inicio:
        raise ValueError(f"ordem {ordem_id}: fim ({fim.isoformat()}) antes do início ({inicio.isoformat()})")
    return Prescricao(
        ordem_id=ordem_id,
        paciente=paciente,
        internacao=internacao,
        medicamento=medicamento.strip(),
        principios=principios_ativos(medicamento),
        dose=interpretar_dose(texto_dose, unidade),
        via=via.strip(),
        via_grupo=grupo_de_via(via),
        frequencia=(frequencia or "").strip() or None,
        tipo_frequencia=tipo_de_frequencia(frequencia, escala_movel=escala_movel),
        doses_24h=doses_24h or None,
        inicio=inicio,
        fim=fim,
        dose_texto=texto_dose,
        unidade_texto=(unidade or "").strip(),
        apresentacao=apresentacao.strip(),
        status=status,
        sintetica=sintetica,
    )
