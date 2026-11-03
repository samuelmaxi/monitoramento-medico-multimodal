"""Unidade, dose, via e frequência de uma prescrição em forma comparável.

Nenhuma comparação de dose acontece antes daqui: ``mcg``, ``mg``, ``g`` e
``gm`` viram ``mg``; ``mL`` e ``L`` viram ``mL``. Unidades sem conversão
física (``UNIT``, ``mEq``, ``mmol``, ``TAB``, ``PUFF``...) passam com o nome
padronizado, e duas doses só são comparáveis se a unidade canônica for a mesma.

A dose do MIMIC é texto: ``"25,000"`` (milhar), ``"2-4"`` (faixa, típica de
PRN), ``"0.5-1"``. Faixas guardam mínimo e máximo; a comparação usa o máximo,
a dose mais alta que a prescrição autoriza.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# unidade (minúscula) → (unidade canônica, fator para a canônica)
_CONVERSOES: dict[str, tuple[str, float]] = {
    "mcg": ("mg", 1e-3),
    "ug": ("mg", 1e-3),
    "µg": ("mg", 1e-3),
    "mg": ("mg", 1.0),
    "g": ("mg", 1e3),
    "gm": ("mg", 1e3),
    "gram": ("mg", 1e3),
    "kg": ("mg", 1e6),
    "ml": ("mL", 1.0),
    "l": ("mL", 1e3),
    "unit": ("UNIT", 1.0),
    "units": ("UNIT", 1.0),
    "meq": ("mEq", 1.0),
    "mmol": ("mmol", 1.0),
    "mcg/h": ("mg/h", 1e-3),
    "mg/h": ("mg/h", 1.0),
    "mg/day": ("mg/dia", 1.0),
}

_RE_NUMERO = r"\d+(?:\.\d+)?|\.\d+"
_RE_DOSE = re.compile(rf"^\s*({_RE_NUMERO})\s*(?:-\s*({_RE_NUMERO}))?\s*$")


@dataclass(frozen=True)
class Dose:
    minimo: float
    maximo: float
    unidade: str
    """Unidade canônica (``mg``, ``mL``, ``UNIT``...)."""

    @property
    def faixa(self) -> bool:
        return self.minimo != self.maximo

    @property
    def valor(self) -> float:
        """Valor usado nas comparações: o máximo autorizado."""
        return self.maximo


def unidade_canonica(unidade: object) -> tuple[str, float] | None:
    """``(unidade canônica, fator)``; ``None`` para unidade vazia."""
    if not isinstance(unidade, str) or not unidade.strip():
        return None
    bruta = unidade.strip()
    return _CONVERSOES.get(bruta.lower(), (bruta, 1.0))


def converter(valor: float, de: str, para: str) -> float | None:
    """Converte entre unidades da mesma dimensão (``mcg`` → ``g``); ``None`` se não dá."""
    origem, destino = unidade_canonica(de), unidade_canonica(para)
    if origem is None or destino is None or origem[0] != destino[0]:
        return None
    return valor * origem[1] / destino[1]


def interpretar_dose(valor: object, unidade: object) -> Dose | None:
    """Dose do MIMIC (texto) na unidade canônica; ``None`` se não for numérica."""
    canonica = unidade_canonica(unidade)
    if canonica is None or valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return None
    texto = str(valor).replace(",", "")
    achado = _RE_DOSE.match(texto)
    if not achado:
        return None
    minimo = float(achado.group(1))
    maximo = float(achado.group(2)) if achado.group(2) else minimo
    if maximo < minimo:
        minimo, maximo = maximo, minimo
    nome, fator = canonica
    return Dose(minimo=minimo * fator, maximo=maximo * fator, unidade=nome)


# --------------------------------------------------------------------------- #
# Via
# --------------------------------------------------------------------------- #

_VIAS: dict[str, str] = {
    **dict.fromkeys(["IV", "IV DRIP", "IV BOLUS", "IVPCA", "IVPB", "IJ"], "IV"),
    **dict.fromkeys(["PO", "PO/NG", "NG", "ORAL", "NU", "OG", "G TUBE", "J TUBE", "PEG"], "enteral"),
    **dict.fromkeys(["SC", "SUBCUT"], "SC"),
    "IM": "IM",
    "SL": "sublingual",
    "PR": "retal",
    "TD": "transdermica",
    **dict.fromkeys(["IH", "NEB", "INH"], "inalatoria"),
    **dict.fromkeys(
        ["TP", "BOTH EYES", "LEFT EYE", "RIGHT EYE", "OS", "LEFT EAR", "RIGHT EAR", "BOTH EARS",
         "NAS", "VG", "ID", "ED"],
        "topica",
    ),
    **dict.fromkeys(["DWELL", "DIALYS", "REPLACE"], "dispositivo"),
}


def grupo_de_via(via: object) -> str:
    """``IV``, ``enteral``, ``SC``, ``IM``, ``transdermica``... ou a via crua em minúsculas."""
    if not isinstance(via, str) or not via.strip():
        return "desconhecida"
    via = via.strip().upper()
    return _VIAS.get(via, via.lower())


# --------------------------------------------------------------------------- #
# Frequência
# --------------------------------------------------------------------------- #

PRN = "prn"
DOSE_UNICA = "dose_unica"
TITULAVEL = "titulavel"
CONFORME_ORIENTACAO = "conforme_orientacao"
AGENDADA = "agendada"
DESCONHECIDA = "desconhecida"

TIPOS_DE_FREQUENCIA = frozenset(
    {PRN, DOSE_UNICA, TITULAVEL, CONFORME_ORIENTACAO, AGENDADA, DESCONHECIDA}
)

_RE_DOSE_UNICA = re.compile(r"\b(ONCE|NOW|STAT|X1|1X|X2|2X|MR ?X?\d|ON CALL)\b")


def tipo_de_frequencia(frequencia: object, *, escala_movel: bool = False) -> str:
    """Classe da frequência do ``pharmacy.frequency``.

    ``prn`` (se necessário), ``dose_unica`` (ONCE, NOW X1, STAT), ``titulavel``
    (TITRATE TO, INFUSION: a dose muda à beira do leito) e
    ``conforme_orientacao`` (ASDIR, escala móvel de insulina) não têm dose
    fixa para comparar; o resto é ``agendada``.
    """
    if not isinstance(frequencia, str) or not frequencia.strip():
        return CONFORME_ORIENTACAO if escala_movel else DESCONHECIDA
    texto = frequencia.strip().upper()
    if "PRN" in texto:
        return PRN
    if "TITRATE" in texto or texto == "INFUSION":
        return TITULAVEL
    if escala_movel or texto == "ASDIR" or "PROTOCOL" in texto:
        return CONFORME_ORIENTACAO
    if _RE_DOSE_UNICA.search(texto):
        return DOSE_UNICA
    return AGENDADA


def dose_diaria(dose: Dose | None, doses_por_24h: object) -> float | None:
    """Dose × administrações em 24 h, quando o MIMIC informa a frequência numérica."""
    if dose is None or not isinstance(doses_por_24h, (int, float)) or math.isnan(doses_por_24h):
        return None
    if doses_por_24h <= 0:
        return None
    return dose.valor * float(doses_por_24h)
