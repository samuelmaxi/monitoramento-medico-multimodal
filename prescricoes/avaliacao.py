"""Recall e falsos positivos sobre os casos injetados (``prescricoes.injecao``).

- Caso positivo detectado: o detector emitiu o ``event_type`` do caso citando a
  ordem injetada em ``ordens``.
- Controle com falso positivo: o mesmo, num caso que não deveria disparar.

    recall = positivos detectados / positivos
    taxa de falso positivo = controles disparados / controles

Anomalias das ordens reais em volta não entram na conta: não têm rótulo.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .config import ConfigPrescricoes
from .injecao import Caso
from .modelos import Internacao
from .regras import detectar


@dataclass
class Contagem:
    positivos: int = 0
    detectados: int = 0
    controles: int = 0
    falsos_positivos: int = 0
    perdidos: list[str] = field(default_factory=list)
    alarmes_falsos: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float | None:
        return self.detectados / self.positivos if self.positivos else None

    @property
    def taxa_falso_positivo(self) -> float | None:
        return self.falsos_positivos / self.controles if self.controles else None

    def para_dict(self) -> dict[str, object]:
        def arred(valor: float | None) -> float | None:
            return None if valor is None else round(valor, 3)

        return {
            "positivos": self.positivos,
            "detectados": self.detectados,
            "recall": arred(self.recall),
            "controles": self.controles,
            "falsos_positivos": self.falsos_positivos,
            "taxa_falso_positivo": arred(self.taxa_falso_positivo),
            "perdidos": self.perdidos,
            "alarmes_falsos": self.alarmes_falsos,
        }


def disparou(caso: Caso, cfg: ConfigPrescricoes, internacoes: Mapping[str, Internacao]) -> bool:
    anomalias = detectar(caso.prescricoes, cfg, internacoes, tipos=[caso.event_type])
    return any(caso.ordem_injetada in a.ordens for a in anomalias)


def avaliar(
    casos: Iterable[Caso], cfg: ConfigPrescricoes, internacoes: Mapping[str, Internacao]
) -> dict[str, dict[str, Contagem]]:
    """Contagens por ``event_type`` e por cenário (``"total"`` agrega o tipo)."""
    resultado: dict[str, dict[str, Contagem]] = {}
    for caso in casos:
        por_tipo = resultado.setdefault(caso.event_type, {"total": Contagem()})
        alvos = (por_tipo["total"], por_tipo.setdefault(caso.cenario, Contagem()))
        achou = disparou(caso, cfg, internacoes)
        for c in alvos:
            if caso.positivo:
                c.positivos += 1
                c.detectados += achou
                if not achou:
                    c.perdidos.append(f"{caso.id}: {caso.descricao}")
            else:
                c.controles += 1
                c.falsos_positivos += achou
                if achou:
                    c.alarmes_falsos.append(f"{caso.id}: {caso.descricao}")
    return resultado
