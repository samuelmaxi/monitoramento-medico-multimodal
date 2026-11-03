"""Princípio(s) ativo(s) a partir do nome comercial/de farmácia do MIMIC.

O MIMIC grava o nome como a farmácia digitou: ``"Metoprolol Succinate XL"``,
``"HYDROmorphone (Dilaudid)"``, ``"OxyCODONE--Acetaminophen (5mg-325mg)"``.
Para comparar duas prescrições é preciso o princípio ativo:

1. sinônimos de nome inteiro (marca → genérico: ``"Tricor"`` → fenofibrate);
2. descarte do que vem depois de ``" - "`` (protocolo) e entre parênteses;
3. combinações separadas por ``-``, ``/``, ``&`` ou ``w/`` viram vários princípios;
4. em cada componente saem concentração, forma farmacêutica e sal
   (``tartrate``, ``sodium``...). O sal só sai depois do primeiro termo:
   ``"Losartan Potassium"`` → losartan, mas ``"Potassium Chloride"`` fica.

O resultado é minúsculo e em inglês, como no MIMIC.
"""

from __future__ import annotations

import re
from functools import lru_cache

# Nome inteiro (já limpo, minúsculo) → princípio(s). Marcas e grafias do demo.
_SINONIMOS: dict[str, tuple[str, ...]] = {
    "tricor": ("fenofibrate",),
    "avapro": ("irbesartan",),
    "januvia": ("sitagliptin",),
    "ambisome": ("amphotericin b",),
    "nexium": ("esomeprazole",),
    "xopenex": ("levalbuterol",),
    "golytely": ("polyethylene glycol",),
    "moviprep": ("polyethylene glycol",),
    "milk of magnesia": ("magnesium hydroxide",),
    "neutra-phos": ("phosphorus",),
    "nephrocaps": ("vitamin b complex",),
    "digox": ("digoxin",),
    "androgel": ("testosterone",),
    "humalog": ("insulin lispro",),
    "insulin": ("insulin",),
    "insulin human regular": ("insulin regular",),
    "insulin regular human": ("insulin regular",),
    "insulin regular": ("insulin regular",),
    "insulin pump": ("insulin",),
    "insulin (regular) for hyperkalemia": ("insulin regular",),
    "furosemide-heart failure": ("furosemide",),
    "filgrastim-sndz": ("filgrastim",),
    "multivitamins w/minerals": ("multivitamins",),
    "multivitamin iv": ("multivitamins",),
    "sulfameth/trimethoprim ds": ("sulfamethoxazole", "trimethoprim"),
    "sulfameth/trimethoprim ss": ("sulfamethoxazole", "trimethoprim"),
    "fleet enema": ("sodium phosphate",),
    "caphosol": ("calcium phosphate",),
    "readi-cat 2": ("barium sulfate",),
}

# Componente (já sem forma/sal) → princípio canônico.
_COMPONENTES: dict[str, str] = {
    "divalproex": "valproate",
    "valproic acid": "valproate",
    "divalproex sod.": "valproate",
    "fosphenytoin": "phenytoin",
    "paracetamol": "acetaminophen",
    "sulfameth": "sulfamethoxazole",
    "ms contin": "morphine",
    "oxycontin": "oxycodone",
    "dilaudid": "hydromorphone",
    "aspirin buffered": "aspirin",
    "vitamin d3": "vitamin d",
    "lenalidomide revlimide": "lenalidomide",
    "insulin glargine": "insulin glargine",
}

_FORMAS = {
    "ec", "sr", "xl", "er", "cr", "ir", "la", "hp", "iv", "odt", "dr", "ds", "ss",
    "liquid", "patch", "oral", "solution", "soln", "soln.", "suspension", "susp", "susp.",
    "tablet", "tab", "elixir", "nasal", "neb", "inhaler", "mdi", "diskus", "cream",
    "ointment", "oint", "gel", "jelly", "lotion", "powder", "spray", "lozenge", "supps",
    "ophth", "ophth.", "viscous", "sprinkles", "extended", "release", "delayed",
    "sustained", "immediate", "disintegrating", "once", "daily", "pca", "infatab",
    "dental", "urojet", "glydo", "replacement", "sliding", "scale", "protocol", "cvicu",
    "reversal", "hyperkalemia", "for", "premix", "inhalation", "vag", "rectal", "enema",
    "nf", "preserv.", "free", "monohyd", "concentrate", "injection", "u-500", "hr",
    "mg", "mcg", "ml", "units", "unit", "xr", "sl", "e.r.", "ophthalmic", "wafer", "rinse",
    "alcohol", "withdrawal", "taper", "dose",
}
_SAIS = {
    "sulfate", "sodium", "potassium", "hcl", "hydrochloride", "tartrate", "succinate",
    "succ", "succ.", "sod", "na", "citrate", "fumarate", "maleate", "mesylate", "besylate",
    "bromide", "acetate", "hyclate", "phosphate", "calcium", "oxalate", "etexilate",
    "proxetil", "xinafoate", "propionate", "gluconate", "carbonate", "acetonide", "nitrate",
    "polacrilex",
}
# Primeiro termo que é o próprio princípio (eletrólito/mineral): o ânion fica.
_CATIONS = {"sodium", "potassium", "calcium", "magnesium", "ferrous", "ferric", "zinc", "aluminum"}

_RE_PARENTESES = re.compile(r"\([^)]*\)?")
_RE_FORCA = re.compile(r"^[\d.,:/%]+[a-z%/]*$|^\d|%")
_RE_COMBINACAO = re.compile(r"\s*(?:--?|/|&|\bw/)\s*")


def _limpar(nome: str) -> str:
    texto = nome.lower().replace("*nf*", " ").replace("_", " ")
    texto = re.split(r"\s+-\s+", texto, maxsplit=1)[0]
    texto = _RE_PARENTESES.sub(" ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _componente(texto: str) -> str | None:
    termos = [t for t in texto.split() if t not in _FORMAS and not _RE_FORCA.search(t)]
    if not termos:
        return None
    if termos[0] not in _CATIONS:
        termos = [termos[0]] + [t for t in termos[1:] if t not in _SAIS]
    nome = " ".join(termos)
    return _COMPONENTES.get(nome, nome)


@lru_cache(maxsize=4096)
def principios_ativos(nome: str) -> tuple[str, ...]:
    """Princípios ativos do nome, sem repetição e em ordem alfabética."""
    if not isinstance(nome, str) or not nome.strip():
        return ()
    bruto = re.sub(r"\s+", " ", nome.lower().replace("_", " ")).strip()
    if bruto in _SINONIMOS:
        return _SINONIMOS[bruto]
    limpo = _limpar(nome)
    if limpo in _SINONIMOS:
        return _SINONIMOS[limpo]
    sem_forma = _componente(limpo)
    if sem_forma in _SINONIMOS:
        return _SINONIMOS[sem_forma]
    if sem_forma is not None and sem_forma.startswith("insulin"):
        # "Insulin Glargine", "Insulin Human Regular": o tipo de insulina importa.
        return (_COMPONENTES.get(sem_forma, sem_forma),)
    partes = [_componente(p) for p in _RE_COMBINACAO.split(limpo) if p.strip()]
    return tuple(sorted({p for p in partes if p}))
