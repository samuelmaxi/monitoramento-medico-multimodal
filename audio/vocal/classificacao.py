"""Regras da US14: de biomarcadores e eventos respiratórios para as classes de alteração vocal.

Cada critério é um predicado sobre os biomarcadores, com limiar vindo do YAML. Uma classe dispara
quando ``minimo`` dos seus critérios (e, se houver, um de ``exigir_um_de``) são satisfeitos. Um
critério sem dado (ex.: taxa de fala sem transcrição) não é avaliável e **não conta a favor**.

Cada classe disparada guarda os critérios satisfeitos com valor, limiar e frase, que viram as
``features``, os ``thresholds`` e o ``summary`` do evento: o achado explica a si mesmo.

Todo resultado é um **indicativo para avaliação clínica, não um diagnóstico**.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from contratos import Severidade

from .baseline import ComparacaoBaseline
from .biomarcadores import BiomarcadoresAudio
from .config import ConfigVocal, Limiares, RegraClasse
from .respiratorio import ResumoRespiratorio

AVISO = "Indicativo para avaliação clínica; não é diagnóstico."
CLASSE_NORMAL = "normal"

LIMIARES_DO_CRITERIO: Mapping[str, tuple[str, ...]] = {
    "eventos_respiratorios": ("eventos_respiratorios_min", "score_respiratorio_forte"),
    "pausas_altas": ("proporcao_pausas_max",),
    "fala_lenta": ("taxa_fala_pps_min",),
    "articulacao_lenta": ("taxa_articulacao_pps_min",),
    "f0_monotona": ("f0_desvio_st_min",),
    "intensidade_caiu": ("intensidade_queda_baseline_db",),
    "jitter_alto": ("jitter_local_pct_max",),
    "shimmer_alto": ("shimmer_local_pct_max",),
    "hnr_baixo": ("hnr_db_min",),
}
"""Quais limiares cada critério usa; só esses entram nos ``thresholds`` do evento."""


@dataclass(frozen=True)
class AvaliacaoCriterio:
    nome: str
    avaliavel: bool
    satisfeito: bool
    valor: float | None
    limiar: float | None
    texto: str

    def para_dict(self) -> dict[str, object]:
        return {
            "criterio": self.nome,
            "avaliavel": self.avaliavel,
            "satisfeito": self.satisfeito,
            "valor": None if self.valor is None else round(self.valor, 4),
            "limiar": self.limiar,
            "texto": self.texto,
        }


@dataclass(frozen=True)
class ClasseDetectada:
    event_type: str
    satisfeitos: tuple[AvaliacaoCriterio, ...]
    nao_avaliados: tuple[str, ...]
    total_criterios: int
    severidade: Severidade
    score: float

    @property
    def regras_acionadas(self) -> str:
        return ", ".join(a.nome for a in self.satisfeitos)

    def para_dict(self) -> dict[str, object]:
        return {
            "event_type": self.event_type,
            "severidade": self.severidade.value,
            "score": self.score,
            "criterios_atendidos": len(self.satisfeitos),
            "criterios_total": self.total_criterios,
            "criterios": [a.para_dict() for a in self.satisfeitos],
            "nao_avaliados": list(self.nao_avaliados),
        }


@dataclass(frozen=True)
class Classificacao:
    classes: tuple[ClasseDetectada, ...]
    avaliacoes: Mapping[str, AvaliacaoCriterio]

    @property
    def classe_principal(self) -> str:
        """A classe de maior severidade (e score); ``normal`` se nenhuma disparou."""
        if not self.classes:
            return CLASSE_NORMAL
        return max(self.classes, key=lambda c: (c.severidade.peso, c.score)).event_type


@dataclass(frozen=True)
class Contexto:
    bio: BiomarcadoresAudio
    respiratorio: ResumoRespiratorio | None
    baseline: ComparacaoBaseline | None
    lim: Limiares


Criterio = Callable[[Contexto], AvaliacaoCriterio]


def _nao_avaliavel(nome: str, motivo: str) -> AvaliacaoCriterio:
    return AvaliacaoCriterio(nome, False, False, None, None, f"{nome}: não avaliável ({motivo})")


def _maior_que(nome: str, valor: float | None, limiar: float, rotulo: str, unidade: str,
               motivo: str = "sem medida neste áudio") -> AvaliacaoCriterio:
    if valor is None:
        return _nao_avaliavel(nome, motivo)
    return AvaliacaoCriterio(
        nome, True, valor > limiar, valor, limiar,
        f"{rotulo} {valor:.2f}{unidade} (limite {limiar:g}{unidade})")


def _menor_que(nome: str, valor: float | None, limiar: float, rotulo: str, unidade: str,
               motivo: str = "sem medida neste áudio") -> AvaliacaoCriterio:
    if valor is None:
        return _nao_avaliavel(nome, motivo)
    return AvaliacaoCriterio(
        nome, True, valor < limiar, valor, limiar,
        f"{rotulo} {valor:.2f}{unidade} (mínimo {limiar:g}{unidade})")


def _eventos_respiratorios(c: Contexto) -> AvaliacaoCriterio:
    nome = "eventos_respiratorios"
    if c.respiratorio is None or not c.respiratorio.disponivel:
        return _nao_avaliavel(nome, "modelo YAMNet não carregado")
    eventos = c.respiratorio.eventos
    n = len(eventos)
    forte = c.respiratorio.score_max >= c.lim.score_respiratorio_forte
    satisfeito = n >= c.lim.eventos_respiratorios_min or forte
    tipos = ", ".join(sorted({e.tipo for e in eventos})) or "nenhum"
    return AvaliacaoCriterio(
        nome, True, satisfeito, float(n), float(c.lim.eventos_respiratorios_min),
        f"{n} evento(s) respiratório(s) ({tipos}; score máx. {c.respiratorio.score_max:.2f}; "
        f"mínimo {c.lim.eventos_respiratorios_min} ou score ≥ {c.lim.score_respiratorio_forte:g})")


def _intensidade_caiu(c: Contexto) -> AvaliacaoCriterio:
    nome = "intensidade_caiu"
    mediana = None if c.baseline is None else c.baseline.medianas.get("intensidade_db")
    if mediana is None or c.bio.intensidade_db is None:
        return _nao_avaliavel(nome, "sem baseline de intensidade do paciente")
    queda = mediana - c.bio.intensidade_db
    limiar = c.lim.intensidade_queda_baseline_db
    return AvaliacaoCriterio(
        nome, True, queda >= limiar, queda, limiar,
        f"intensidade {queda:.1f} dB abaixo da baseline do paciente (queda mínima {limiar:g} dB)")


def _pausas_altas(c: Contexto) -> AvaliacaoCriterio:
    proporcao, limiar = c.bio.proporcao_pausas, c.lim.proporcao_pausas_max
    return AvaliacaoCriterio(
        "pausas_altas", True, proporcao > limiar, proporcao, limiar,
        f"pausas ocupam {proporcao:.0%} da fala (limite {limiar:.0%})")


CRITERIOS_IMPLEMENTADOS: Mapping[str, Criterio] = {
    "eventos_respiratorios": _eventos_respiratorios,
    "pausas_altas": _pausas_altas,
    "fala_lenta": lambda c: _menor_que(
        "fala_lenta", c.bio.taxa_fala_pps, c.lim.taxa_fala_pps_min, "taxa de fala", " palavras/s",
        "sem transcrição"),
    "articulacao_lenta": lambda c: _menor_que(
        "articulacao_lenta", c.bio.taxa_articulacao_pps, c.lim.taxa_articulacao_pps_min,
        "taxa de articulação", " palavras/s", "sem transcrição"),
    "f0_monotona": lambda c: _menor_que(
        "f0_monotona", c.bio.f0_desvio_st, c.lim.f0_desvio_st_min, "variação de F0", " st",
        "sem voz sustentada suficiente"),
    "intensidade_caiu": _intensidade_caiu,
    "jitter_alto": lambda c: _maior_que(
        "jitter_alto", c.bio.jitter_local_pct, c.lim.jitter_local_pct_max, "jitter local", " %",
        "sem voz sustentada suficiente"),
    "shimmer_alto": lambda c: _maior_que(
        "shimmer_alto", c.bio.shimmer_local_pct, c.lim.shimmer_local_pct_max, "shimmer local", " %",
        "sem voz sustentada suficiente"),
    "hnr_baixo": lambda c: _menor_que(
        "hnr_baixo", c.bio.hnr_db, c.lim.hnr_db_min, "HNR", " dB", "sem voz sustentada suficiente"),
}

def _avaliar_classe(
    regra: RegraClasse, avaliacoes: Mapping[str, AvaliacaoCriterio]
) -> ClasseDetectada | None:
    satisfeitos = tuple(avaliacoes[n] for n in regra.criterios if avaliacoes[n].satisfeito)
    if len(satisfeitos) < regra.minimo:
        return None
    if regra.exigir_um_de and not any(a.nome in regra.exigir_um_de for a in satisfeitos):
        return None
    forte = regra.forte is not None and len(satisfeitos) >= regra.forte
    severidade = regra.severidade_forte if forte and regra.severidade_forte else regra.severidade
    return ClasseDetectada(
        event_type=regra.event_type,
        satisfeitos=satisfeitos,
        nao_avaliados=tuple(n for n in regra.criterios if not avaliacoes[n].avaliavel),
        total_criterios=len(regra.criterios),
        severidade=severidade,
        score=round(len(satisfeitos) / len(regra.criterios), 4),
    )


def classificar(
    bio: BiomarcadoresAudio,
    respiratorio: ResumoRespiratorio | None,
    baseline: ComparacaoBaseline | None,
    cfg: ConfigVocal,
) -> Classificacao:
    """Avalia todos os critérios uma vez e aplica as regras de cada classe."""
    contexto = Contexto(bio, respiratorio, baseline, cfg.limiares)
    usados = {n for regra in cfg.regras.values() for n in regra.criterios}
    avaliacoes = {nome: CRITERIOS_IMPLEMENTADOS[nome](contexto) for nome in sorted(usados)}
    classes = tuple(
        classe for regra in cfg.regras.values()
        if (classe := _avaliar_classe(regra, avaliacoes)) is not None
    )
    return Classificacao(classes=classes, avaliacoes=avaliacoes)


def limiares_da_classe(regra: RegraClasse, lim: Limiares) -> dict[str, float | int]:
    """Os limiares que a classe usa, com os nomes do YAML, para o ``evidence.thresholds``."""
    resultado: dict[str, float | int] = {}
    for criterio in regra.criterios:
        for chave in LIMIARES_DO_CRITERIO[criterio]:
            resultado[chave] = getattr(lim, chave)
    return resultado
