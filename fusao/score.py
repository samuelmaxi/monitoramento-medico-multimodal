"""Cálculo do score de risco por paciente: late fusion ponderada (funções puras).

Passos, todos parametrizados em ``config/us18_fusao.yaml``:

1. **Força de cada achado** (0–1)::

       forca = peso(modalidade, tipo) × valor_severidade × (1 - α + α·score) × decaimento

   A severidade é a base porque é o único campo com a mesma semântica em todos
   os módulos; o ``score`` só modula (α = ``influencia_score``).

2. **Sinal da modalidade** = maior força entre os seus achados. Máximo, e não
   soma, porque achados do mesmo sensor são correlacionados: dez frames do mesmo
   desvio não são dez evidências independentes.

3. **Concordância clínica**: se 2+ modalidades apontam para o mesmo eixo
   (respiratório, neurológico, queda), entra um termo extra
   ``fator × 2ª maior força do eixo``.

4. **Combinação noisy-OR**: ``risco = 1 - Π(1 - termo)``. Cada termo é a chance
   de deterioração indicada por aquela fonte; o risco só existe se alguma delas
   estiver certa. Propriedades que a DoD pede:

   - modalidade ausente vira fator 1, sem renormalizar e sem falhar;
   - com dois ou mais sinais, o risco é sempre maior que o maior sinal isolado;
   - o resultado fica em [0, 1] sem truncamento.

5. **Contribuição por modalidade** (explicabilidade): o risco é repartido na
   proporção de ``-ln(1 - termo)``, a evidência de cada termo na escala em que
   o noisy-OR é aditivo. As contribuições somam exatamente o risco.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from contratos import EventoAchado, Modalidade, Severidade

from .alinhamento import Alinhamento, idade
from .config import MODALIDADES_FUNDIDAS, ConfigFusao, MembroEixo

TERMO_MAXIMO = 0.999  # evita ln(0) se alguém configurar peso e severidade em 1,0

NIVEIS = ("baixo", "medio", "alto")
SEVERIDADE_DO_NIVEL = {
    "baixo": Severidade.BAIXA, "medio": Severidade.MEDIA, "alto": Severidade.ALTA,
}


@dataclass(frozen=True)
class SinalModalidade:
    modalidade: Modalidade
    forca: float
    """Risco que esta modalidade indicaria sozinha (0 se ausente ou normal)."""
    contribuicao: float
    """Parcela do score final atribuída a ela."""
    evento: EventoAchado | None
    """Achado que definiu a força (o mais forte da janela)."""


@dataclass(frozen=True)
class Concordancia:
    eixo: str
    modalidades: tuple[Modalidade, ...]
    termo: float
    contribuicao: float


@dataclass(frozen=True)
class Risco:
    patient_id: str
    inicio: datetime
    ate: datetime
    score: float
    nivel: str
    severidade: Severidade
    sinais: dict[Modalidade, SinalModalidade]
    concordancia: Concordancia | None
    presentes: tuple[Modalidade, ...]
    ausentes: tuple[Modalidade, ...]
    com_achado: tuple[Modalidade, ...]
    eventos: tuple[EventoAchado, ...]

    @property
    def score_max_isolado(self) -> float:
        return max((s.forca for s in self.sinais.values()), default=0.0)

    @property
    def ganho_fusao(self) -> float:
        """Quanto a combinação somou acima do sinal isolado mais forte."""
        return max(0.0, self.score - self.score_max_isolado)

    @property
    def contribuicoes(self) -> dict[str, float]:
        """Contribuição de cada modalidade e da concordância; soma = ``score``."""
        resultado = {m.value: s.contribuicao for m, s in self.sinais.items()}
        resultado["concordancia"] = self.concordancia.contribuicao if self.concordancia else 0.0
        return resultado


# --------------------------------------------------------------------------- #
# Passo 1 e 2: força
# --------------------------------------------------------------------------- #

def decaimento(evento: EventoAchado, ate: datetime, config: ConfigFusao) -> float:
    if config.meia_vida is None:
        return 1.0
    return 0.5 ** (idade(evento, ate) / config.meia_vida)


def forca_do_achado(evento: EventoAchado, ate: datetime, config: ConfigFusao) -> float:
    base = config.valor_severidade[evento.severity]
    if base == 0:
        return 0.0
    alfa = config.influencia_score
    return (
        config.peso(evento.modality, evento.event_type)
        * base
        * (1 - alfa + alfa * evento.score)
        * decaimento(evento, ate, config)
    )


def _mais_forte(
    eventos: tuple[EventoAchado, ...], ate: datetime, config: ConfigFusao
) -> tuple[float, EventoAchado | None]:
    melhor, escolhido = 0.0, None
    for evento in eventos:
        forca = forca_do_achado(evento, ate, config)
        if forca > melhor:
            melhor, escolhido = forca, evento
    return melhor, escolhido


# --------------------------------------------------------------------------- #
# Passo 3: concordância
# --------------------------------------------------------------------------- #

def _pertence(evento: EventoAchado, membro: MembroEixo) -> bool:
    if evento.event_type not in membro.tipos:
        return False
    if membro.termos is None:
        return True
    termo = evento.evidence.features.get("termo")
    return isinstance(termo, str) and termo.casefold() in membro.termos


def concordancia(
    alinhamento: Alinhamento, config: ConfigFusao
) -> tuple[str, tuple[Modalidade, ...], float] | None:
    """Eixo clínico de maior concordância: ``(eixo, modalidades, termo)`` ou ``None``."""
    if config.fator_concordancia == 0:
        return None
    melhor = None
    for nome, membros in config.eixos.items():
        forcas: list[tuple[float, Modalidade]] = []
        for membro in membros:
            eventos = alinhamento.por_modalidade.get(membro.modalidade, ())
            candidatos = tuple(e for e in eventos if _pertence(e, membro))
            forca, _ = _mais_forte(candidatos, alinhamento.ate, config)
            if forca > 0:
                forcas.append((forca, membro.modalidade))
        if len(forcas) < 2:
            continue
        forcas.sort(key=lambda par: par[0], reverse=True)
        termo = config.fator_concordancia * forcas[1][0]
        if melhor is None or termo > melhor[2]:
            melhor = (nome, tuple(m for _, m in forcas), termo)
    return melhor


# --------------------------------------------------------------------------- #
# Passo 4 e 5: combinação e atribuição
# --------------------------------------------------------------------------- #

def noisy_or(termos: list[float]) -> float:
    produto = 1.0
    for termo in termos:
        produto *= 1 - min(max(termo, 0.0), TERMO_MAXIMO)
    return 1 - produto


def atribuir(termos: list[float], total: float) -> list[float]:
    """Reparte ``total`` na proporção de ``-ln(1 - termo)``."""
    evidencias = [-math.log1p(-min(max(t, 0.0), TERMO_MAXIMO)) for t in termos]
    evidencias = [max(0.0, e) for e in evidencias]  # -0.0 de termo nulo vira 0.0
    soma = sum(evidencias)
    if soma == 0:
        return [0.0] * len(termos)
    return [total * e / soma for e in evidencias]


def nivel_do_score(score: float, config: ConfigFusao) -> str:
    if score >= config.nivel_alto:
        return "alto"
    if score >= config.nivel_medio:
        return "medio"
    return "baixo"


def calcular_risco(alinhamento: Alinhamento, config: ConfigFusao) -> Risco:
    """Score de risco do paciente na janela do alinhamento."""
    forcas: dict[Modalidade, tuple[float, EventoAchado | None]] = {
        m: _mais_forte(alinhamento.por_modalidade.get(m, ()), alinhamento.ate, config)
        for m in MODALIDADES_FUNDIDAS
    }
    conc = concordancia(alinhamento, config)

    termos = [forcas[m][0] for m in MODALIDADES_FUNDIDAS]
    if conc is not None:
        termos.append(conc[2])
    score = noisy_or(termos)
    partes = atribuir(termos, score)

    sinais = {
        m: SinalModalidade(m, forcas[m][0], partes[i], forcas[m][1])
        for i, m in enumerate(MODALIDADES_FUNDIDAS)
    }
    concordancia_final = (
        Concordancia(conc[0], conc[1], conc[2], partes[-1]) if conc is not None else None
    )

    com_achado = tuple(m for m in MODALIDADES_FUNDIDAS if forcas[m][0] > 0)
    nivel = nivel_do_score(score, config)
    severidade = SEVERIDADE_DO_NIVEL[nivel]
    if score == 0 or (len(com_achado) < 2 and not config.alertar_com_uma_modalidade):
        severidade = Severidade.INFO

    return Risco(
        patient_id=alinhamento.patient_id,
        inicio=alinhamento.inicio,
        ate=alinhamento.ate,
        score=score,
        nivel=nivel,
        severidade=severidade,
        sinais=sinais,
        concordancia=concordancia_final,
        presentes=alinhamento.presentes,
        ausentes=alinhamento.ausentes,
        com_achado=com_achado,
        eventos=alinhamento.eventos,
    )
