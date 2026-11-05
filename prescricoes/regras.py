"""Os quatro detectores da US16 sobre as prescrições de uma internação.

==========================  ===================================================
``mudanca_abrupta_dose``    nova ordem do mesmo produto e via, até ``janela_h``
                            depois da anterior, com dose ±``variacao_min_pct``
``duplicidade_terapeutica`` duas ordens com o mesmo princípio ativo, em via
                            sistêmica, sobrepostas por ``sobreposicao_min_h``
``suspensao_abrupta``       princípio que exige desmame, em uso contínuo, que
                            some sem desmame, sem troca na classe e sem alta
``medicamento_alta_vigilancia``  primeira ordem na internação de um princípio
                            da lista ISMP
==========================  ===================================================

Toda anomalia leva uma justificativa legível (``resumo``) com a regra e os
valores antes × depois, que também vão estruturados em ``antes``/``depois``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta

from contratos import Severidade

from .config import ConfigPrescricoes, RegraDuplicidade
from .modelos import Anomalia, Internacao, Prescricao
from .normalizacao import DOSE_UNICA, PRN

MUDANCA_DOSE = "mudanca_abrupta_dose"
DUPLICIDADE = "duplicidade_terapeutica"
SUSPENSAO = "suspensao_abrupta"
ALTA_VIGILANCIA = "medicamento_alta_vigilancia"
TIPOS = (MUDANCA_DOSE, DUPLICIDADE, SUSPENSAO, ALTA_VIGILANCIA)

_SCORE_POR_SEVERIDADE = {
    Severidade.INFO: 0.2,
    Severidade.BAIXA: 0.4,
    Severidade.MEDIA: 0.6,
    Severidade.ALTA: 0.8,
}


# --------------------------------------------------------------------------- #
# Formatação da justificativa
# --------------------------------------------------------------------------- #


def _n(valor: float) -> str:
    casas = 2 if abs(valor) >= 1 or valor == 0 else 4  # 0,025 mg não pode virar 0,03
    texto = f"{valor:.{casas}f}".rstrip("0").rstrip(".")
    return texto.replace(".", ",")


def _horas(delta: timedelta) -> str:
    horas = delta.total_seconds() / 3600
    return f"{_n(horas)} h" if horas < 48 else f"{_n(horas / 24)} dias"


def _dose(p: Prescricao) -> str:
    if p.dose is None:
        return f"{p.dose_texto} {p.unidade_texto}".strip() or "dose não informada"
    texto = f"{_n(p.dose.valor)} {p.dose.unidade}"
    if p.frequencia:
        texto += f" {p.frequencia}"
    if p.dose_diaria is not None:
        texto += f" ({_n(p.dose_diaria)} {p.dose.unidade}/dia)"
    return texto


def _fim_efetivo(p: Prescricao, internacao: Internacao | None) -> datetime:
    """Fim da ordem; sem fim no MIMIC, vale a alta (ou o próprio início)."""
    if p.fim is not None:
        return p.fim
    if internacao is not None and internacao.alta is not None:
        return max(internacao.alta, p.inicio)
    return p.inicio


def _score(excesso: float, limiar: float, saturacao: float) -> float:
    """0,5 no limiar, 1,0 na saturação."""
    fracao = (excesso - limiar) / (saturacao - limiar)
    return round(0.5 + 0.5 * min(1.0, max(0.0, fracao)), 4)


_TROCA_DE_ORDEM = "inactive (due to a change order)"


def _encerrar_trocas(prescricoes: list[Prescricao]) -> list[Prescricao]:
    """Fecha a ordem substituída no início da que a substituiu.

    Quando a prescrição é alterada, o MIMIC marca a antiga como ``Inactive (Due
    to a change order)`` mas mantém o ``stoptime`` original: sem este ajuste a
    antiga e a nova pareceriam ativas em paralelo (duplicidade falsa) e a dose
    vigente seria a soma das duas.
    """
    seguintes: dict[tuple[str, str], list[datetime]] = {}
    for p in prescricoes:
        if p.tipo_frequencia != DOSE_UNICA:  # dose avulsa não substitui a ordem
            seguintes.setdefault((p.medicamento.lower(), p.via_grupo), []).append(p.inicio)
    ajustadas = []
    for p in prescricoes:
        if (p.status or "").lower() == _TROCA_DE_ORDEM and p.fim is not None:
            proxima = min(
                (
                    t
                    for t in seguintes.get((p.medicamento.lower(), p.via_grupo), ())
                    if p.inicio < t < p.fim
                ),
                default=None,
            )
            if proxima is not None:
                p = replace(p, fim=proxima)
        ajustadas.append(p)
    return ajustadas


def _terapeuticas(prescricoes: Iterable[Prescricao], cfg: ConfigPrescricoes) -> list[Prescricao]:
    """Ordens de tratamento que chegaram a valer (sem flush/dwell, sem canceladas)."""
    return _encerrar_trocas(
        [
            p
            for p in prescricoes
            if p.principios and cfg.geral.terapeutico(p.medicamento) and p.fim != p.inicio
        ]
    )


def _classe_ismp(p: Prescricao, cfg: ConfigPrescricoes) -> str | None:
    classe = cfg.alta_vigilancia.classe(p.principios, p.via_grupo, p.medicamento, p.apresentacao)
    return None if classe is None else classe.id


# --------------------------------------------------------------------------- #
# Mudança abrupta de dose
# --------------------------------------------------------------------------- #


def _comparar_doses(
    antes: Sequence[Prescricao], depois: Sequence[Prescricao]
) -> tuple[float, float, str] | None:
    """``(dose antes, dose depois, base)`` de dois regimes, ou ``None``.

    O regime vale a soma das ordens (varfarina 2,5 mg + 1 mg = 3,5 mg). Compara
    a dose diária quando todas a têm; senão a dose por administração, se a
    frequência for a mesma em todas.
    """
    ordens = [*antes, *depois]
    if any(p.dose is None for p in ordens) or len({p.dose.unidade for p in ordens}) != 1:
        return None
    if all(p.dose_diaria is not None for p in ordens):
        return (
            sum(p.dose_diaria for p in antes),
            sum(p.dose_diaria for p in depois),
            "dose_diaria",
        )
    if len({(p.frequencia or "").upper() for p in ordens}) == 1:
        return (
            sum(p.dose.valor for p in antes),
            sum(p.dose.valor for p in depois),
            "dose_por_administracao",
        )
    return None


def _regime(
    ordens: Sequence[Prescricao],
    instante: datetime,
    internacao: Internacao | None,
    janela: timedelta,
    transicao: timedelta,
) -> tuple[list[Prescricao], list[Prescricao]] | None:
    """Ordens vigentes logo antes e logo depois de ``instante``.

    O regime é o conjunto de ordens ativas do produto: insulina 30 UI à noite +
    13 UI no café valem 43 UI/dia. "Antes" é o regime até ``instante`` ou, se
    houve intervalo sem ordem, o último regime que terminou até ``janela``
    antes. Uma ordem antiga que termina até ``transicao`` depois da nova foi
    substituída e não entra no "depois" (é a mesma tolerância da duplicidade).
    ``None`` se nada vigorou nesse período.
    """
    anteriores = [p for p in ordens if p.inicio < instante]
    if not anteriores:
        return None
    ultimo_fim = max(_fim_efetivo(p, internacao) for p in anteriores)
    if ultimo_fim < instante - janela:
        return None
    referencia = min(instante, ultimo_fim)
    antes = [
        p for p in anteriores if p.inicio < referencia and _fim_efetivo(p, internacao) >= referencia
    ]
    depois = [
        p
        for p in ordens
        if p.inicio <= instante
        and _fim_efetivo(p, internacao) > instante + (transicao if p.inicio < instante else timedelta(0))
    ]
    if not antes or not depois:
        return None
    return antes, depois


def _so_duplicou(antes: Sequence[Prescricao], depois: Sequence[Prescricao]) -> bool:
    """O regime só ganhou cópia de uma ordem que continua: é duplicidade, não troca de dose."""
    ids_antes = {p.ordem_id for p in antes}
    if not ids_antes <= {p.ordem_id for p in depois}:
        return False
    chaves = {(p.medicamento.lower(), (p.frequencia or "").upper()) for p in antes}
    return all(
        (p.medicamento.lower(), (p.frequencia or "").upper()) in chaves
        for p in depois
        if p.ordem_id not in ids_antes
    )


def _doses(regime: Sequence[Prescricao]) -> str:
    return " + ".join(_dose(p) for p in regime)


def mudancas_de_dose(
    prescricoes: Sequence[Prescricao],
    cfg: ConfigPrescricoes,
    internacao: Internacao | None = None,
) -> list[Anomalia]:
    regra = cfg.dose
    janela = timedelta(hours=regra.janela_h)
    transicao = timedelta(hours=cfg.duplicidade.sobreposicao_min_h)
    grupos: dict[tuple[tuple[str, ...], str], list[Prescricao]] = {}
    for p in _terapeuticas(prescricoes, cfg):
        if p.tipo_frequencia in regra.frequencias_comparaveis and p.dose is not None:
            grupos.setdefault((p.principios, p.via_grupo), []).append(p)

    anomalias = []
    for (principios, via), ordens in grupos.items():
        for inicio_novo in sorted({p.inicio for p in ordens}):
            regime = _regime(ordens, inicio_novo, internacao, janela, transicao)
            if regime is None:
                continue
            anterior, atual = regime
            if {p.ordem_id for p in anterior} == {p.ordem_id for p in atual}:
                continue
            if _so_duplicou(anterior, atual):
                continue
            comparacao = _comparar_doses(anterior, atual)
            if comparacao is None:
                continue
            valor_antes, valor_depois, base = comparacao
            if valor_antes <= 0:
                continue
            variacao = (valor_depois - valor_antes) / valor_antes * 100
            if abs(variacao) < regra.variacao_min_pct:
                continue
            novas = [p for p in atual if p.inicio == inicio_novo]
            depois = novas[0] if novas else atual[-1]
            antes = max(anterior, key=lambda p: p.inicio)
            fim_anterior = max(_fim_efetivo(p, internacao) for p in anterior)
            classe = _classe_ismp(depois, cfg)
            severidade = regra.severidade_alta_vigilancia if classe else regra.severidade
            defasagem = max(timedelta(0), inicio_novo - fim_anterior)
            sentido = "subiu" if variacao > 0 else "caiu"
            unidade = depois.dose.unidade + ("/dia" if base == "dose_diaria" else "")
            quando = (
                "na troca de prescrição"
                if defasagem == timedelta(0)
                else f"{_horas(defasagem)} depois do fim da anterior"
            )
            anomalias.append(
                Anomalia(
                    event_type=MUDANCA_DOSE,
                    regra="dose.variacao",
                    severidade=severidade,
                    score=_score(abs(variacao), regra.variacao_min_pct, regra.saturacao_pct),
                    paciente=depois.paciente,
                    internacao=depois.internacao,
                    instante=inicio_novo,
                    inicio=min(inicio_novo, fim_anterior),
                    fim=inicio_novo,
                    principio="+".join(principios),
                    resumo=(
                        f"Dose de {depois.medicamento} ({via}) {sentido} {_n(abs(variacao))}% "
                        f"({_n(valor_antes)} → {_n(valor_depois)} {unidade}), {quando}. "
                        f"Antes: {_doses(anterior)}; depois: {_doses(atual)}. "
                        f"Limiar: ±{_n(regra.variacao_min_pct)}% em {_n(regra.janela_h)} h."
                    ),
                    antes=antes.resumo(),
                    depois=depois.resumo(),
                    features={
                        "variacao_pct": round(variacao, 2),
                        "base_comparacao": base,
                        "valor_antes": round(valor_antes, 4),
                        "valor_depois": round(valor_depois, 4),
                        "unidade_comparacao": unidade,
                        "defasagem_h": round(defasagem.total_seconds() / 3600, 2),
                        "ordens_antes": len(anterior),
                        "ordens_depois": len(atual),
                        "via_grupo": via,
                        "classe_ismp": classe,
                    },
                    limiares={
                        "variacao_min_pct": regra.variacao_min_pct,
                        "janela_h": regra.janela_h,
                    },
                    ordens=tuple(dict.fromkeys(p.ordem_id for p in (*anterior, *atual))),
                )
            )
    return anomalias


# --------------------------------------------------------------------------- #
# Duplicidade terapêutica
# --------------------------------------------------------------------------- #


def _paralelo_esperado(ativa: Prescricao, nova: Prescricao, regra: RegraDuplicidade) -> bool:
    """Pares do mesmo princípio em paralelo que fazem parte de um mesmo esquema.

    - esquema fracionado: mesmo produto, as duas agendadas, com frequências
      diferentes (insulina 30 UI à noite + 13 UI no café; varfarina 3x/semana
      + 4x/semana) ou dose composta no mesmo início (varfarina 2,5 + 1 mg);
    - resgate: mesmo produto (mesmos princípios) com uma das ordens PRN, o
      esquema fixo + resgate ou a via alternativa (paracetamol VO ou VR).
      Combinação × isolado (oxicodona+paracetamol com paracetamol) continua.

    Mesmo produto, mesma frequência e inícios diferentes (anlodipino 10 mg/dia
    duas vezes), ou outra via (lorazepam VO e IV a cada 8 h), é duplicidade.
    """
    mesmo_produto = ativa.medicamento.lower() == nova.medicamento.lower()
    if mesmo_produto and ativa.tipo_frequencia == nova.tipo_frequencia == "agendada":
        mesma_frequencia = (ativa.frequencia or "").upper() == (nova.frequencia or "").upper()
        if not mesma_frequencia or ativa.inicio == nova.inicio:
            return True
    return (
        regra.ignorar_resgate_mesmo_produto
        and ativa.principios == nova.principios
        and PRN in (ativa.tipo_frequencia, nova.tipo_frequencia)
    )


def duplicidades(
    prescricoes: Sequence[Prescricao],
    cfg: ConfigPrescricoes,
    internacao: Internacao | None = None,
) -> list[Anomalia]:
    regra = cfg.duplicidade
    minimo = timedelta(hours=regra.sobreposicao_min_h)
    candidatas = [
        p
        for p in _terapeuticas(prescricoes, cfg)
        if p.via_grupo in cfg.geral.vias_sistemicas
        and p.tipo_frequencia not in regra.frequencias_ignoradas
    ]
    por_principio: dict[str, list[Prescricao]] = {}
    for p in candidatas:
        for principio in p.principios:
            if principio not in regra.principios_permitidos:
                por_principio.setdefault(principio, []).append(p)

    anomalias = []
    for principio, ordens in por_principio.items():
        ordens.sort(key=lambda p: (p.inicio, p.ordem_id))
        for i, nova in enumerate(ordens):
            fim_nova = _fim_efetivo(nova, internacao)
            melhor: tuple[timedelta, Prescricao] | None = None
            for ativa in ordens[:i]:
                if _paralelo_esperado(ativa, nova, regra):
                    continue
                sobreposicao = min(fim_nova, _fim_efetivo(ativa, internacao)) - nova.inicio
                if sobreposicao >= minimo and (melhor is None or sobreposicao > melhor[0]):
                    melhor = (sobreposicao, ativa)
            if melhor is None:
                continue
            sobreposicao, ativa = melhor
            classe = _classe_ismp(nova, cfg) or _classe_ismp(ativa, cfg)
            ambas_agendadas = PRN not in (ativa.tipo_frequencia, nova.tipo_frequencia)
            severidade = (
                regra.severidade_alta_vigilancia if classe and ambas_agendadas else regra.severidade
            )
            fim = nova.inicio + sobreposicao
            mesmo_produto = ativa.medicamento.lower() == nova.medicamento.lower()
            descricao = (
                f"{nova.medicamento} prescrito duas vezes"
                if mesmo_produto
                else f"{principio} em {ativa.medicamento} e em {nova.medicamento}"
            )
            anomalias.append(
                Anomalia(
                    event_type=DUPLICIDADE,
                    regra="duplicidade.mesmo_principio",
                    severidade=severidade,
                    score=_score(
                        sobreposicao.total_seconds() / 3600,
                        regra.sobreposicao_min_h,
                        max(24.0, 4 * regra.sobreposicao_min_h),
                    ),
                    paciente=nova.paciente,
                    internacao=nova.internacao,
                    instante=nova.inicio,
                    inicio=nova.inicio,
                    fim=fim,
                    principio=principio,
                    resumo=(
                        f"Duplicidade terapêutica: {descricao}, ativos em paralelo por "
                        f"{_horas(sobreposicao)}. Já ativo: {_dose(ativa)} {ativa.via}; "
                        f"novo: {_dose(nova)} {nova.via}. "
                        f"Limiar: sobreposição ≥ {_n(regra.sobreposicao_min_h)} h."
                    ),
                    antes=ativa.resumo(),
                    depois=nova.resumo(),
                    features={
                        "sobreposicao_h": round(sobreposicao.total_seconds() / 3600, 2),
                        "mesmo_produto": mesmo_produto,
                        "via_ativa": ativa.via_grupo,
                        "via_nova": nova.via_grupo,
                        "classe_ismp": classe,
                    },
                    limiares={"sobreposicao_min_h": regra.sobreposicao_min_h},
                    ordens=(ativa.ordem_id, nova.ordem_id),
                )
            )
    return anomalias


# --------------------------------------------------------------------------- #
# Suspensão abrupta
# --------------------------------------------------------------------------- #


def _episodios(
    ordens: Sequence[Prescricao], tolerancia: timedelta, internacao: Internacao | None
) -> list[list[Prescricao]]:
    """Ordens agrupadas em cursos contínuos (lacuna ≤ ``tolerancia``)."""
    episodios: list[list[Prescricao]] = []
    fim_atual: datetime | None = None
    for p in sorted(ordens, key=lambda p: p.inicio):
        if fim_atual is None or p.inicio > fim_atual + tolerancia:
            episodios.append([p])
            fim_atual = _fim_efetivo(p, internacao)
        else:
            episodios[-1].append(p)
            fim_atual = max(fim_atual, _fim_efetivo(p, internacao))
    return episodios


def _intensidade(p: Prescricao) -> float | None:
    if p.dose_diaria is not None:
        return p.dose_diaria
    return None if p.dose is None else p.dose.valor


def suspensoes(
    prescricoes: Sequence[Prescricao],
    cfg: ConfigPrescricoes,
    internacao: Internacao | None = None,
) -> list[Anomalia]:
    regra = cfg.suspensao
    if internacao is None or internacao.alta is None:
        return []  # sem a alta não dá para separar suspensão de fim da internação
    sem_retorno = timedelta(hours=regra.sem_retorno_h)
    sistemicas = [
        p for p in _terapeuticas(prescricoes, cfg) if p.via_grupo in cfg.geral.vias_sistemicas
    ]
    classes = {c: cfg.classes_terapeuticas[c] for c in regra.classes}

    anomalias = []
    for classe, principios_da_classe in classes.items():
        for principio in sorted(principios_da_classe):
            todas = [p for p in sistemicas if principio in p.principios]
            agendadas = [p for p in todas if p.tipo_frequencia == "agendada"]
            for curso in _episodios(agendadas, sem_retorno, internacao):
                inicio = curso[0].inicio
                fim = max(_fim_efetivo(p, internacao) for p in curso)
                if (fim - inicio).total_seconds() / 3600 < regra.uso_continuo_min_h:
                    continue
                ultima = max(curso, key=lambda p: (_fim_efetivo(p, internacao), p.inicio))
                if (ultima.status or "").lower().endswith("via patient discharge"):
                    continue
                limite = fim + sem_retorno
                if internacao.alta < limite or (internacao.obito and internacao.obito < limite):
                    continue  # suspensão na alta/óbito, não durante a internação
                if any(
                    p.tipo_frequencia != DOSE_UNICA
                    and p.inicio <= limite
                    and _fim_efetivo(p, internacao) > fim
                    for p in todas
                ):
                    continue  # voltou (ou continuou como PRN)
                if regra.troca_na_classe_conta and any(
                    p.inicio <= limite
                    and _fim_efetivo(p, internacao) > fim
                    and principio not in p.principios
                    and principios_da_classe.intersection(p.principios)
                    for p in sistemicas
                ):
                    continue  # troca por outro da mesma classe
                intensidades = [i for i in (_intensidade(p) for p in curso) if i is not None]
                ultima_dose = _intensidade(ultima)
                if intensidades and ultima_dose is not None:
                    pico = max(intensidades)
                    if pico > 0 and (pico - ultima_dose) / pico * 100 >= regra.desmame_reducao_min_pct:
                        continue  # houve desmame
                duracao = fim - inicio
                anomalias.append(
                    Anomalia(
                        event_type=SUSPENSAO,
                        regra=f"suspensao.{classe}",
                        severidade=regra.severidade,
                        score=_score(
                            duracao.total_seconds() / 3600,
                            regra.uso_continuo_min_h,
                            4 * regra.uso_continuo_min_h,
                        ),
                        paciente=ultima.paciente,
                        internacao=ultima.internacao,
                        instante=fim,
                        inicio=fim,
                        fim=limite,
                        principio=principio,
                        resumo=(
                            f"Suspensão abrupta de {principio} ({classe}, exige desmame): uso "
                            f"contínuo por {_horas(duracao)}, última ordem {_dose(ultima)} "
                            f"{ultima.via}, sem redução prévia e sem retorno ou substituto na "
                            f"classe em {_n(regra.sem_retorno_h)} h, com o paciente internado. "
                            f"Antes: {_dose(ultima)}; depois: nenhuma prescrição."
                        ),
                        antes=ultima.resumo(),
                        depois=None,
                        features={
                            "classe_terapeutica": classe,
                            "uso_continuo_h": round(duracao.total_seconds() / 3600, 2),
                            "ordens_no_curso": len(curso),
                            "horas_ate_alta": round(
                                (internacao.alta - fim).total_seconds() / 3600, 2
                            ),
                            "classe_ismp": _classe_ismp(ultima, cfg),
                        },
                        limiares={
                            "uso_continuo_min_h": regra.uso_continuo_min_h,
                            "sem_retorno_h": regra.sem_retorno_h,
                            "desmame_reducao_min_pct": regra.desmame_reducao_min_pct,
                        },
                        ordens=tuple(p.ordem_id for p in curso),
                    )
                )
    return anomalias


# --------------------------------------------------------------------------- #
# Introdução de medicamento de alta vigilância
# --------------------------------------------------------------------------- #


def introducoes_alta_vigilancia(
    prescricoes: Sequence[Prescricao],
    cfg: ConfigPrescricoes,
    internacao: Internacao | None = None,
) -> list[Anomalia]:
    vistos: set[tuple[str, str]] = set()
    anomalias = []
    for p in sorted(_terapeuticas(prescricoes, cfg), key=lambda p: (p.inicio, p.ordem_id)):
        classe = cfg.alta_vigilancia.classe(p.principios, p.via_grupo, p.medicamento, p.apresentacao)
        if classe is None:
            continue
        principio = "+".join(sorted(classe.principios.intersection(p.principios)))
        if (classe.id, principio) in vistos:
            continue
        vistos.add((classe.id, principio))
        horas_admissao = (
            None
            if internacao is None
            else round((p.inicio - internacao.admissao).total_seconds() / 3600, 2)
        )
        anomalias.append(
            Anomalia(
                event_type=ALTA_VIGILANCIA,
                regra=f"ismp.{classe.id}",
                severidade=classe.severidade,
                score=_SCORE_POR_SEVERIDADE[classe.severidade],
                paciente=p.paciente,
                internacao=p.internacao,
                instante=p.inicio,
                inicio=p.inicio,
                fim=max(p.inicio, _fim_efetivo(p, internacao)),
                principio=principio,
                resumo=(
                    f"Introdução de {p.medicamento} {p.via} ({classe.id.replace('_', ' ')}, "
                    f"lista {cfg.alta_vigilancia.lista}): {_dose(p)}. "
                    f"Antes: sem {principio} nesta internação; depois: {_dose(p)}."
                ),
                antes=None,
                depois=p.resumo(),
                features={
                    "classe_ismp": classe.id,
                    "horas_desde_admissao": horas_admissao,
                    "tipo_frequencia": p.tipo_frequencia,
                },
                limiares={"lista": cfg.alta_vigilancia.lista},
                ordens=(p.ordem_id,),
            )
        )
    return anomalias


DETECTORES = {
    MUDANCA_DOSE: mudancas_de_dose,
    DUPLICIDADE: duplicidades,
    SUSPENSAO: suspensoes,
    ALTA_VIGILANCIA: introducoes_alta_vigilancia,
}


def detectar(
    prescricoes: Iterable[Prescricao],
    cfg: ConfigPrescricoes,
    internacoes: Mapping[str, Internacao] | None = None,
    *,
    tipos: Iterable[str] = TIPOS,
) -> list[Anomalia]:
    """Roda os detectores por internação; anomalias em ordem de tempo."""
    grupos: dict[tuple[str, str | None], list[Prescricao]] = {}
    for p in prescricoes:
        grupos.setdefault((p.paciente, p.internacao), []).append(p)
    internacoes = internacoes or {}
    anomalias = []
    for (_, hadm), ordens in grupos.items():
        internacao = internacoes.get(hadm)
        for tipo in tipos:
            anomalias.extend(DETECTORES[tipo](ordens, cfg, internacao))
    return sorted(anomalias, key=lambda a: (a.paciente, a.instante, a.event_type))
