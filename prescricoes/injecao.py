"""Casos rotulados por injeção em internações reais do MIMIC-IV Demo.

Cada caso parte das prescrições reais de uma internação e acrescenta (ou
corta) uma ordem marcada ``sintetica=True`` com ``ordem_id`` ``inj-...``. O
detector roda sobre a internação inteira, com todo o ruído real em volta.

Positivos (o detector precisa achar, citando a ordem injetada):

=================  ======================================================
``dose_aumento``    ordem agendada real cortada no meio e continuada com
                    dose × 1,5–4 (sem intervalo ou com até 20 h)
``dose_reducao``    idem, dose × 0,1–0,5
``duplicidade``     cópia de uma ordem agendada real (mesmo produto e
                    frequência, ou outra via), começando com ela ativa e
                    sobrepondo 8 h a 3 dias
``suspensao``       curso de 3,5–8 dias de um medicamento que exige
                    desmame, terminando ≥ 30 h antes da alta, sem retorno
``alta_vigilancia`` primeira ordem de um medicamento ISMP ausente da internação
=================  ======================================================

Controles (o detector NÃO pode disparar o tipo citando a ordem injetada):

=========================  =================================================
``dose_abaixo_limiar``      variação de 10–40%
``dose_fora_da_janela``     dose × 2, mas 30–72 h depois do fim da anterior
``troca_sem_sobreposicao``  mesma cópia da duplicidade, começando quando a
                            original termina (ou até 4 h antes)
``suspensao_com_desmame``   curso que termina com a dose reduzida à metade
``suspensao_na_alta``       curso que termina até 12 h antes da alta
``introducao_comum``        primeira ordem de medicamento fora da lista ISMP
=========================  =================================================

O rótulo é o que a regra da US16 define como anomalia; o caso testa se a
implementação acha esse padrão no meio de dados reais. Não substitui rótulo
clínico (ver docs/relatorio_us16_dod.md).
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta

from .config import ConfigPrescricoes
from .entrada import criar_prescricao
from .modelos import Internacao, Prescricao
from .normalizacao import AGENDADA, grupo_de_via, interpretar_dose
from .principios import principios_ativos
from .regras import ALTA_VIGILANCIA, DUPLICIDADE, MUDANCA_DOSE, SUSPENSAO

H = timedelta(hours=1)


@dataclass(frozen=True)
class Caso:
    id: str
    cenario: str
    event_type: str
    """Tipo que o caso exercita."""
    positivo: bool
    paciente: str
    internacao: str
    ordem_injetada: str
    prescricoes: tuple[Prescricao, ...]
    descricao: str


# Medicamentos injetados: (nome no estilo MIMIC, dose, unidade, via, frequência, doses/24 h).
_ALTA_VIGILANCIA = [
    ("Heparin", "5000", "UNIT", "SC", "Q8H", 3),
    ("Enoxaparin Sodium", "40", "mg", "SC", "DAILY", 1),
    ("Warfarin", "5", "mg", "PO", "DAILY16", 1),
    ("Apixaban", "5", "mg", "PO", "BID", 2),
    ("Morphine Sulfate", "2", "mg", "IV", "Q4H:PRN", None),
    ("HYDROmorphone (Dilaudid)", "0.5", "mg", "IV", "Q3H:PRN", None),
    ("OxyCODONE (Immediate Release)", "5", "mg", "PO", "Q4H:PRN", None),
    ("Fentanyl Patch", "25", "mcg/h", "TD", "Q72H", None),
    ("Insulin Glargine", "10", "UNIT", "SC", "HS", 1),
    ("Insulin Human Regular", "4", "UNIT", "SC", "ASDIR", None),
    ("Potassium Chloride", "20", "mEq", "IV", "ONCE", None),
    ("Propofol", "1000", "mg", "IV DRIP", "TITRATE TO", None),
    ("Midazolam", "2", "mg", "IV", "Q2H:PRN", None),
    ("NORepinephrine", "8", "mg", "IV DRIP", "TITRATE TO", None),
    ("Rocuronium", "50", "mg", "IV", "ONCE", None),
    ("Amiodarone", "150", "mg", "IV", "ONCE", None),
    ("GlipiZIDE", "5", "mg", "PO", "DAILY", 1),
    ("Methadone", "10", "mg", "PO", "Q8H", 3),
]
# KCl concentrado: a apresentação é que o põe na lista.
_APRESENTACAO = {"Potassium Chloride": "2mEq/mL-20mL"}

_COMUNS = [
    ("Pantoprazole", "40", "mg", "PO", "Q24H", 1),
    ("Atorvastatin", "40", "mg", "PO", "QPM", 1),
    ("Senna", "8.6", "mg", "PO", "BID:PRN", None),
    ("Cefazolin", "2", "g", "IV", "Q8H", 3),
    ("Amlodipine", "5", "mg", "PO", "DAILY", 1),
    ("Ondansetron", "4", "mg", "IV", "Q8H:PRN", None),
]

# Cursos que exigem desmame: (nome, dose, unidade, via, frequência, doses/24 h).
_DESMAME = [
    ("Metoprolol Tartrate", 25, "mg", "PO", "BID", 2),
    ("Carvedilol", 6.25, "mg", "PO", "BID", 2),
    ("Propranolol", 20, "mg", "PO", "TID", 3),
    ("PredniSONE", 40, "mg", "PO", "DAILY", 1),
    ("Dexamethasone", 4, "mg", "IV", "Q6H", 4),
    ("Hydrocortisone Na Succ.", 50, "mg", "IV", "Q6H", 4),
    ("CloniDINE", 0.1, "mg", "PO", "TID", 3),
    ("LORazepam", 1, "mg", "PO", "Q8H", 3),
    ("ClonazePAM", 0.5, "mg", "PO", "BID", 2),
    ("LeVETiracetam", 500, "mg", "PO", "BID", 2),
    ("Phenytoin", 100, "mg", "PO", "TID", 3),
    ("Baclofen", 10, "mg", "PO", "TID", 3),
    ("OxyCODONE SR (OxyconTIN)", 10, "mg", "PO", "Q12H", 2),
]


def _nova(
    base: Internacao,
    ordem_id: str,
    nome: str,
    dose: str,
    unidade: str,
    via: str,
    frequencia: str,
    doses_24h: float | None,
    inicio,
    fim,
    *,
    apresentacao: str = "",
) -> Prescricao:
    return criar_prescricao(
        ordem_id=ordem_id,
        paciente=base.paciente,
        internacao=base.id,
        medicamento=nome,
        dose=dose,
        unidade=unidade,
        via=via,
        frequencia=frequencia,
        doses_24h=doses_24h,
        inicio=inicio,
        fim=fim,
        apresentacao=apresentacao or _APRESENTACAO.get(nome, ""),
        status="Discontinued",
        sintetica=True,
    )


def _copia(p: Prescricao, ordem_id: str, **campos) -> Prescricao:
    return replace(p, ordem_id=ordem_id, sintetica=True, status="Discontinued", **campos)


def _escalada(p: Prescricao, fator: float, ordem_id: str, inicio, fim) -> Prescricao:
    valor = float(p.dose_texto.replace(",", "").split("-")[-1]) * fator
    texto = f"{valor:.4f}".rstrip("0").rstrip(".")
    return _copia(
        p,
        ordem_id,
        dose=interpretar_dose(texto, p.unidade_texto),
        dose_texto=texto,
        inicio=inicio,
        fim=fim,
    )


class Gerador:
    """Sorteia casos de cada cenário a partir das internações reais."""

    def __init__(
        self,
        prescricoes: Sequence[Prescricao],
        internacoes: Mapping[str, Internacao],
        cfg: ConfigPrescricoes,
        *,
        semente: int = 0,
        controlado: bool = True,
    ) -> None:
        self.cfg = cfg
        self.controlado = controlado
        self.rng = random.Random(semente)
        self.internacoes = internacoes
        self.por_internacao: dict[str, list[Prescricao]] = {}
        for p in prescricoes:
            if p.internacao in internacoes:
                self.por_internacao.setdefault(p.internacao, []).append(p)
        self._n = 0

    # ------------------------------------------------------------------ util

    def _id(self) -> str:
        self._n += 1
        return f"inj-{self._n:05d}"

    def _caso(
        self, cenario: str, tipo: str, positivo: bool, hadm: str, ordens: list[Prescricao],
        injetada: str, descricao: str,
    ) -> Caso:
        return Caso(
            id=f"{cenario}-{injetada}",
            cenario=cenario,
            event_type=tipo,
            positivo=positivo,
            paciente=self.internacoes[hadm].paciente,
            internacao=hadm,
            ordem_injetada=injetada,
            prescricoes=tuple(sorted(ordens, key=lambda p: (p.inicio, p.ordem_id))),
            descricao=descricao,
        )

    def _agendadas(self, *, horas_min: float) -> list[tuple[str, Prescricao]]:
        """Ordens agendadas reais com dose numérica simples e duração mínima.

        Modo controlado: só ordens que são o regime inteiro do produto naquela
        via (nenhuma outra agendada em paralelo) e que não foram substituídas,
        para o rótulo (dose × fator, cópia em paralelo) ser o que o regime viu.
        Modo realista (``controlado=False``): qualquer ordem, e o rótulo pode
        divergir do regime (dose × 0,3 de uma das duas ordens de valproato).
        """
        saida = []
        for hadm, ordens in self.por_internacao.items():
            for p in ordens:
                if self.controlado and (
                    (p.status or "").startswith("Inactive") or self._acompanhada(hadm, p)
                ):
                    continue
                if (
                    p.tipo_frequencia == AGENDADA
                    and p.dose is not None
                    and not p.dose.faixa
                    and p.dose_diaria is not None
                    and p.fim is not None
                    and (p.fim - p.inicio) >= horas_min * H
                    and p.via_grupo in self.cfg.geral.vias_sistemicas
                    and self.cfg.geral.terapeutico(p.medicamento)
                    and not set(p.principios) & self.cfg.duplicidade.principios_permitidos
                ):
                    saida.append((hadm, p))
        return saida

    def _acompanhada(self, hadm: str, p: Prescricao) -> bool:
        fim = p.fim or p.inicio
        return any(
            o.ordem_id != p.ordem_id
            and o.principios == p.principios
            and o.via_grupo == p.via_grupo
            and o.tipo_frequencia == AGENDADA
            and o.inicio < fim
            and (o.fim is None or o.fim > p.inicio)
            for o in self.por_internacao[hadm]
        )

    def _trocar(self, hadm: str, original: Prescricao, *novas: Prescricao, corte=None) -> list[Prescricao]:
        ordens = [p for p in self.por_internacao[hadm] if p.ordem_id != original.ordem_id]
        if corte is not None:
            ordens.append(replace(original, fim=corte, status="Inactive (Due to a change order)"))
        else:
            ordens.append(original)
        return ordens + list(novas)

    def _internacoes_longas(self, dias: float) -> list[str]:
        return [
            h
            for h, i in self.internacoes.items()
            if i.alta is not None and (i.alta - i.admissao) >= timedelta(days=dias)
            and i.obito is None and h in self.por_internacao
        ]

    # ------------------------------------------------------------- cenários

    def dose(self, cenario: str) -> Caso:
        hadm, p = self.rng.choice(self._agendadas(horas_min=24))
        duracao = p.fim - p.inicio
        corte = p.inicio + duracao * self.rng.uniform(0.3, 0.6)
        if cenario == "dose_aumento":
            fator, lacuna, positivo = self.rng.uniform(1.5, 4.0), self.rng.uniform(0, 20), True
        elif cenario == "dose_reducao":
            fator, lacuna, positivo = self.rng.uniform(0.1, 0.5), self.rng.uniform(0, 20), True
        elif cenario == "dose_abaixo_limiar":
            fator = 1 + self.rng.choice([-1, 1]) * self.rng.uniform(0.1, 0.4)
            lacuna, positivo = self.rng.uniform(0, 20), False
        else:  # dose_fora_da_janela
            fator, lacuna, positivo = 2.0, self.rng.uniform(30, 72), False
        inicio = corte + lacuna * H
        nova = _escalada(p, fator, self._id(), inicio, inicio + max(duracao - (corte - p.inicio), 24 * H))
        ordens = self._trocar(hadm, p, nova, corte=corte)
        if cenario == "dose_fora_da_janela":
            # Nada do mesmo produto e via pode cobrir o intervalo.
            ordens = [
                o for o in ordens
                if o is nova or o.principios != p.principios or o.via_grupo != p.via_grupo
                or o.fim is not None and o.fim <= corte or o.inicio >= inicio
            ]
        return self._caso(
            cenario, MUDANCA_DOSE, positivo, hadm, ordens, nova.ordem_id,
            f"{p.medicamento}: dose × {fator:.2f}, {lacuna:.0f} h depois do corte",
        )

    def duplicidade(self, cenario: str) -> Caso:
        hadm, p = self.rng.choice(self._agendadas(horas_min=12))
        duracao_h = (p.fim - p.inicio).total_seconds() / 3600
        outra_via = self.rng.random() < 0.3
        via = {"enteral": "IV", "IV": "PO"}.get(p.via_grupo, p.via) if outra_via else p.via
        if cenario == "duplicidade":
            sobreposicao = self.rng.uniform(8, 72)
            inicio = p.inicio + self.rng.uniform(1, max(1.0, duracao_h - 8)) * H
            fim = inicio + sobreposicao * H
            positivo = True
        else:  # troca_sem_sobreposicao
            inicio = p.fim - self.rng.uniform(0, 4) * H
            fim = inicio + self.rng.uniform(24, 72) * H
            positivo = False
        copia = _copia(p, self._id(), inicio=inicio, fim=fim, via=via, via_grupo=grupo_de_via(via))
        ordens = self._trocar(hadm, p, copia)
        if not positivo:
            # Controle: nada real do mesmo princípio pode sobrepor a cópia.
            ordens = [
                o for o in ordens
                if o is copia or o is p or not set(o.principios) & set(p.principios)
                or (o.fim is not None and o.fim <= inicio) or o.inicio >= fim
            ]
        return self._caso(
            cenario, DUPLICIDADE, positivo, hadm, ordens, copia.ordem_id,
            f"{p.medicamento} {via}: cópia de {inicio:%d %Hh} a {fim:%d %Hh}",
        )

    def _sem_classe(self, hadm: str, principios: tuple[str, ...]) -> bool:
        classe = next(
            (
                c
                for c in self.cfg.suspensao.classes
                if self.cfg.classes_terapeuticas[c].intersection(principios)
            ),
            None,
        )
        alvo = self.cfg.classes_terapeuticas[classe] if classe else set(principios)
        return not any(alvo.intersection(o.principios) for o in self.por_internacao[hadm])

    def suspensao(self, cenario: str) -> Caso:
        for _ in range(200):
            hadm = self.rng.choice(self._internacoes_longas(6))
            nome, dose, unidade, via, freq, n = self.rng.choice(_DESMAME)
            if self._sem_classe(hadm, principios_ativos(nome)):
                break
        internacao = self.internacoes[hadm]
        duracao = self.rng.uniform(3.5, 8) * 24 * H
        if cenario == "suspensao_na_alta":
            fim = internacao.alta - self.rng.uniform(0, 12) * H
        else:
            folga = (internacao.alta - internacao.admissao) - duracao - 30 * H
            fim = internacao.alta - 30 * H - folga * self.rng.uniform(0, 1) if folga > timedelta(0) else internacao.alta - 30 * H
        inicio = max(internacao.admissao, fim - duracao)
        ordens = list(self.por_internacao[hadm])
        if cenario == "suspensao_com_desmame":
            meio = inicio + (fim - inicio) * 0.7
            curso = [
                _nova(internacao, self._id(), nome, f"{dose:g}", unidade, via, freq, n, inicio, meio),
                _nova(internacao, self._id(), nome, f"{dose / 2:g}", unidade, via, freq, n, meio, fim),
            ]
        else:
            curso = [_nova(internacao, self._id(), nome, f"{dose:g}", unidade, via, freq, n, inicio, fim)]
        positivo = cenario == "suspensao"
        return self._caso(
            cenario, SUSPENSAO, positivo, hadm, ordens + curso, curso[-1].ordem_id,
            f"{nome} {via} por {(fim - inicio).total_seconds() / 86400:.1f} dias, "
            f"fim {(internacao.alta - fim).total_seconds() / 3600:.0f} h antes da alta",
        )

    def introducao(self, cenario: str) -> Caso:
        lista = _ALTA_VIGILANCIA if cenario == "alta_vigilancia" else _COMUNS
        for _ in range(200):
            hadm = self.rng.choice(list(self.por_internacao))
            nome, dose, unidade, via, freq, n = self.rng.choice(lista)
            principios = principios_ativos(nome)
            if not any(set(principios) & set(o.principios) for o in self.por_internacao[hadm]):
                break
        internacao = self.internacoes[hadm]
        fim_internacao = internacao.alta or internacao.admissao + 72 * H
        inicio = internacao.admissao + (fim_internacao - internacao.admissao) * self.rng.uniform(0.05, 0.8)
        nova = _nova(internacao, self._id(), nome, dose, unidade, via, freq, n, inicio, inicio + 24 * H)
        return self._caso(
            cenario, ALTA_VIGILANCIA, cenario == "alta_vigilancia", hadm,
            self.por_internacao[hadm] + [nova], nova.ordem_id, f"{nome} {via} introduzido",
        )

    CENARIOS: Mapping[str, tuple[str, str]] = {
        "dose_aumento": ("dose", MUDANCA_DOSE),
        "dose_reducao": ("dose", MUDANCA_DOSE),
        "dose_abaixo_limiar": ("dose", MUDANCA_DOSE),
        "dose_fora_da_janela": ("dose", MUDANCA_DOSE),
        "duplicidade": ("duplicidade", DUPLICIDADE),
        "troca_sem_sobreposicao": ("duplicidade", DUPLICIDADE),
        "suspensao": ("suspensao", SUSPENSAO),
        "suspensao_com_desmame": ("suspensao", SUSPENSAO),
        "suspensao_na_alta": ("suspensao", SUSPENSAO),
        "alta_vigilancia": ("introducao", ALTA_VIGILANCIA),
        "introducao_comum": ("introducao", ALTA_VIGILANCIA),
    }

    def gerar(self, cenario: str) -> Caso:
        metodo: Callable[[str], Caso] = getattr(self, self.CENARIOS[cenario][0])
        return metodo(cenario)

    def lote(self, por_cenario: int) -> list[Caso]:
        return [self.gerar(c) for c in self.CENARIOS for _ in range(por_cenario)]
