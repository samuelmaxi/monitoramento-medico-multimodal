"""Leitura do ``audio/vocal/config.yaml`` (US12/US14): limiares, regras por classe e baseline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from audio.configuracao import (
    ErroDeConfiguracao,
    booleano,
    inteiro,
    ler_yaml,
    lista_de_textos,
    mapa,
    numero,
    obter,
    sem_chaves_extras,
    texto,
)
from contratos import Modalidade, Severidade, tipos_validos

__all__ = [
    "CAMINHO_PADRAO",
    "CLASSES_VOCAIS",
    "CRITERIOS",
    "BaselineCfg",
    "ClasseRespiratoria",
    "ConfigVocal",
    "ErroDeConfiguracao",
    "Limiares",
    "PausasCfg",
    "PraatCfg",
    "QualidadeCfg",
    "RegraClasse",
    "RespiratorioCfg",
    "SegmentosCfg",
    "carregar_config",
    "config_de_dict",
]

CAMINHO_PADRAO = Path("audio/vocal/config.yaml")

CLASSES_VOCAIS = frozenset({"fadiga_vocal", "dificuldade_respiratoria", "possivel_disartria"})
"""Os três ``event_type`` de classe da US14; "normal" é ``sem_achados``."""

CRITERIOS = frozenset({
    "eventos_respiratorios", "pausas_altas", "fala_lenta", "articulacao_lenta", "f0_monotona",
    "intensidade_caiu", "jitter_alto", "shimmer_alto", "hnr_baixo",
})
"""Critérios implementados em :mod:`audio.vocal.classificacao`; a config só escolhe entre eles."""


@dataclass(frozen=True)
class PraatCfg:
    pitch_floor_hz: float
    pitch_ceiling_hz: float
    passo_s: float
    periodo_minimo_s: float
    periodo_maximo_s: float
    fator_maximo_periodo: float
    fator_maximo_amplitude: float
    hnr_limiar_silencio: float
    hnr_periodos_por_janela: float
    vozeado_minimo_s: float


@dataclass(frozen=True)
class VadCfg:
    janela_ms: float
    pausa_entre_segmentos_s: float


@dataclass(frozen=True)
class SegmentosCfg:
    duracao_minima_s: float
    vad: VadCfg


@dataclass(frozen=True)
class PausasCfg:
    janela_ms: float
    limiar_relativo_db: float
    duracao_minima_s: float
    nivel_minimo_fala_db: float


@dataclass(frozen=True)
class QualidadeCfg:
    formatos_com_perdas: frozenset[str]


@dataclass(frozen=True)
class ClasseRespiratoria:
    classe_audioset: str
    tipo: str
    severidade: Severidade


@dataclass(frozen=True)
class RespiratorioCfg:
    score_minimo: float
    lacuna_quadros: int
    max_eventos: int
    classes: tuple[ClasseRespiratoria, ...]


@dataclass(frozen=True)
class Limiares:
    jitter_local_pct_max: float
    shimmer_local_pct_max: float
    hnr_db_min: float
    proporcao_pausas_max: float
    taxa_fala_pps_min: float
    taxa_articulacao_pps_min: float
    f0_desvio_st_min: float
    intensidade_queda_baseline_db: float
    eventos_respiratorios_min: int
    score_respiratorio_forte: float


@dataclass(frozen=True)
class RegraClasse:
    event_type: str
    criterios: tuple[str, ...]
    minimo: int
    forte: int | None
    exigir_um_de: tuple[str, ...]
    severidade: Severidade
    severidade_forte: Severidade | None


@dataclass(frozen=True)
class BaselineCfg:
    audios_minimos: int
    audios_maximos: int
    z_minimo: float
    excluir_audios_alterados: bool
    severidade: Severidade
    severidade_forte: Severidade
    metricas_fortes: int
    metricas: Mapping[str, float]
    """Métrica → piso da dispersão (na unidade da métrica)."""


@dataclass(frozen=True)
class ConfigVocal:
    versao: str
    praat: PraatCfg
    segmentos: SegmentosCfg
    pausas: PausasCfg
    respiratorio: RespiratorioCfg
    qualidade: QualidadeCfg
    limiares: Limiares
    regras: Mapping[str, RegraClasse]
    baseline: BaselineCfg
    fonte: Path | None = None


def _severidade(valor: Any, caminho: str) -> Severidade:
    try:
        return Severidade(valor)
    except ValueError:
        validas = ", ".join(s.value for s in Severidade)
        raise ErroDeConfiguracao(f"'{caminho}' = {valor!r}; use {validas}") from None


def _praat(bruto: Any) -> PraatCfg:
    c = "praat"
    d = mapa(bruto, c)
    sem_chaves_extras(d, {
        "pitch_floor_hz", "pitch_ceiling_hz", "passo_s", "periodo_minimo_s", "periodo_maximo_s",
        "fator_maximo_periodo", "fator_maximo_amplitude", "hnr_limiar_silencio",
        "hnr_periodos_por_janela", "vozeado_minimo_s",
    }, c)

    def n(chave: str, **limites: Any) -> float:
        return numero(obter(d, chave, c), f"{c}.{chave}", minimo=limites.pop("minimo", 0),
                      minimo_exclusivo=True, **limites)

    piso, teto = n("pitch_floor_hz"), n("pitch_ceiling_hz")
    if teto <= piso:
        raise ErroDeConfiguracao(f"'{c}.pitch_ceiling_hz' precisa ser maior que pitch_floor_hz")
    minimo, maximo = n("periodo_minimo_s"), n("periodo_maximo_s")
    if maximo <= minimo:
        raise ErroDeConfiguracao(f"'{c}.periodo_maximo_s' precisa ser maior que periodo_minimo_s")
    return PraatCfg(
        pitch_floor_hz=piso, pitch_ceiling_hz=teto, passo_s=n("passo_s"),
        periodo_minimo_s=minimo, periodo_maximo_s=maximo,
        fator_maximo_periodo=n("fator_maximo_periodo", minimo=1),
        fator_maximo_amplitude=n("fator_maximo_amplitude", minimo=1),
        hnr_limiar_silencio=n("hnr_limiar_silencio", maximo=1),
        hnr_periodos_por_janela=n("hnr_periodos_por_janela"),
        vozeado_minimo_s=n("vozeado_minimo_s"),
    )


def _segmentos(bruto: Any) -> SegmentosCfg:
    c = "segmentos"
    d = mapa(bruto, c)
    sem_chaves_extras(d, {"duracao_minima_s", "vad"}, c)
    vad = mapa(obter(d, "vad", c), f"{c}.vad")
    sem_chaves_extras(vad, {"janela_ms", "pausa_entre_segmentos_s"}, f"{c}.vad")
    return SegmentosCfg(
        duracao_minima_s=numero(obter(d, "duracao_minima_s", c), f"{c}.duracao_minima_s",
                                minimo=0, minimo_exclusivo=True),
        vad=VadCfg(
            janela_ms=numero(obter(vad, "janela_ms", c), f"{c}.vad.janela_ms", minimo=1),
            pausa_entre_segmentos_s=numero(obter(vad, "pausa_entre_segmentos_s", c),
                                           f"{c}.vad.pausa_entre_segmentos_s", minimo=0,
                                           minimo_exclusivo=True),
        ),
    )


def _pausas(bruto: Any) -> PausasCfg:
    c = "pausas"
    d = mapa(bruto, c)
    sem_chaves_extras(
        d, {"janela_ms", "limiar_relativo_db", "duracao_minima_s", "nivel_minimo_fala_db"}, c)
    return PausasCfg(
        janela_ms=numero(obter(d, "janela_ms", c), f"{c}.janela_ms", minimo=1),
        limiar_relativo_db=numero(obter(d, "limiar_relativo_db", c), f"{c}.limiar_relativo_db",
                                  maximo=-1),
        duracao_minima_s=numero(obter(d, "duracao_minima_s", c), f"{c}.duracao_minima_s",
                                minimo=0, minimo_exclusivo=True),
        nivel_minimo_fala_db=numero(obter(d, "nivel_minimo_fala_db", c),
                                    f"{c}.nivel_minimo_fala_db", maximo=0),
    )


def _respiratorio(bruto: Any) -> RespiratorioCfg:
    c = "respiratorio"
    d = mapa(bruto, c)
    sem_chaves_extras(d, {"score_minimo", "lacuna_quadros", "max_eventos", "classes"}, c)
    classes = []
    for nome, regra in mapa(obter(d, "classes", c), f"{c}.classes").items():
        aqui = f"{c}.classes.{nome}"
        regra = mapa(regra, aqui)
        sem_chaves_extras(regra, {"tipo", "severidade"}, aqui)
        classes.append(ClasseRespiratoria(
            classe_audioset=str(nome),
            tipo=texto(obter(regra, "tipo", aqui), f"{aqui}.tipo"),
            severidade=_severidade(obter(regra, "severidade", aqui), f"{aqui}.severidade"),
        ))
    if not classes:
        raise ErroDeConfiguracao(f"'{c}.classes' está vazio")
    return RespiratorioCfg(
        score_minimo=numero(obter(d, "score_minimo", c), f"{c}.score_minimo", minimo=0,
                            maximo=1, minimo_exclusivo=True),
        lacuna_quadros=inteiro(obter(d, "lacuna_quadros", c), f"{c}.lacuna_quadros", minimo=0),
        max_eventos=inteiro(obter(d, "max_eventos", c), f"{c}.max_eventos", minimo=1),
        classes=tuple(classes),
    )


def _qualidade(bruto: Any) -> QualidadeCfg:
    c = "qualidade"
    d = mapa(bruto, c)
    sem_chaves_extras(d, {"formatos_com_perdas"}, c)
    formatos = lista_de_textos(obter(d, "formatos_com_perdas", c), f"{c}.formatos_com_perdas",
                               vazia=True)
    return QualidadeCfg(frozenset(f.lower().lstrip(".") for f in formatos))


_LIMIARES_INTEIROS = {"eventos_respiratorios_min"}


def _limiares(bruto: Any) -> Limiares:
    c = "limiares"
    d = mapa(bruto, c)
    campos = set(Limiares.__dataclass_fields__)
    sem_chaves_extras(d, campos, c)
    valores: dict[str, float | int] = {}
    for campo in sorted(campos):
        bruto_valor = obter(d, campo, c)
        if campo in _LIMIARES_INTEIROS:
            valores[campo] = inteiro(bruto_valor, f"{c}.{campo}", minimo=1)
        else:
            valores[campo] = numero(bruto_valor, f"{c}.{campo}", minimo=0)
    for fracao in ("proporcao_pausas_max", "score_respiratorio_forte"):
        if not 0 < valores[fracao] <= 1:
            raise ErroDeConfiguracao(f"'{c}.{fracao}' precisa estar em (0, 1]")
    return Limiares(**valores)  # type: ignore[arg-type]


def _classes(bruto: Any) -> dict[str, RegraClasse]:
    c = "classes"
    d = mapa(bruto, c)
    desconhecidas = set(d) - CLASSES_VOCAIS
    if desconhecidas:
        raise ErroDeConfiguracao(
            f"'{c}': classes desconhecidas {sorted(desconhecidas)}; use {sorted(CLASSES_VOCAIS)}"
        )
    faltando = CLASSES_VOCAIS - set(d)
    if faltando:
        raise ErroDeConfiguracao(f"'{c}': faltam as classes {sorted(faltando)}")
    catalogo = tipos_validos(Modalidade.AUDIO)
    regras: dict[str, RegraClasse] = {}
    for tipo, bruto_regra in d.items():
        aqui = f"{c}.{tipo}"
        if tipo not in catalogo:
            raise ErroDeConfiguracao(f"'{aqui}': event_type fora do catálogo de áudio")
        regra = mapa(bruto_regra, aqui)
        sem_chaves_extras(regra, {"criterios", "minimo", "forte", "exigir_um_de", "severidade",
                                  "severidade_forte"}, aqui)
        criterios = lista_de_textos(obter(regra, "criterios", aqui), f"{aqui}.criterios")
        exigir = lista_de_textos(regra.get("exigir_um_de", []), f"{aqui}.exigir_um_de", vazia=True)
        for nome in (*criterios, *exigir):
            if nome not in CRITERIOS:
                raise ErroDeConfiguracao(
                    f"'{aqui}': critério desconhecido '{nome}'. Válidos: {sorted(CRITERIOS)}"
                )
        if len(set(criterios)) != len(criterios):
            raise ErroDeConfiguracao(f"'{aqui}.criterios' tem itens repetidos")
        if not set(exigir) <= set(criterios):
            raise ErroDeConfiguracao(f"'{aqui}.exigir_um_de' precisa estar contido em criterios")
        minimo = inteiro(obter(regra, "minimo", aqui), f"{aqui}.minimo", minimo=1)
        if minimo > len(criterios):
            raise ErroDeConfiguracao(f"'{aqui}.minimo' ({minimo}) excede os critérios")
        forte = None
        if "forte" in regra:
            forte = inteiro(regra["forte"], f"{aqui}.forte", minimo=minimo)
        if forte is not None and forte > len(criterios):
            raise ErroDeConfiguracao(f"'{aqui}.forte' ({forte}) excede os critérios")
        if (forte is None) != ("severidade_forte" not in regra):
            raise ErroDeConfiguracao(f"'{aqui}': 'forte' e 'severidade_forte' vêm juntos")
        regras[tipo] = RegraClasse(
            event_type=tipo, criterios=criterios, minimo=minimo, forte=forte, exigir_um_de=exigir,
            severidade=_severidade(obter(regra, "severidade", aqui), f"{aqui}.severidade"),
            severidade_forte=_severidade(regra["severidade_forte"], f"{aqui}.severidade_forte")
            if forte is not None else None,
        )
    return regras


def _baseline(bruto: Any) -> BaselineCfg:
    c = "baseline"
    d = mapa(bruto, c)
    sem_chaves_extras(d, {
        "audios_minimos", "audios_maximos", "z_minimo", "excluir_audios_alterados", "severidade",
        "severidade_forte", "metricas_fortes", "metricas",
    }, c)
    minimos = inteiro(obter(d, "audios_minimos", c), f"{c}.audios_minimos", minimo=2)
    maximos = inteiro(obter(d, "audios_maximos", c), f"{c}.audios_maximos", minimo=minimos)
    metricas = {
        str(nome): numero(piso, f"{c}.metricas.{nome}", minimo=0, minimo_exclusivo=True)
        for nome, piso in mapa(obter(d, "metricas", c), f"{c}.metricas").items()
    }
    if not metricas:
        raise ErroDeConfiguracao(f"'{c}.metricas' está vazio")
    return BaselineCfg(
        audios_minimos=minimos,
        audios_maximos=maximos,
        z_minimo=numero(obter(d, "z_minimo", c), f"{c}.z_minimo", minimo=0, minimo_exclusivo=True),
        excluir_audios_alterados=booleano(obter(d, "excluir_audios_alterados", c),
                                          f"{c}.excluir_audios_alterados"),
        severidade=_severidade(obter(d, "severidade", c), f"{c}.severidade"),
        severidade_forte=_severidade(obter(d, "severidade_forte", c), f"{c}.severidade_forte"),
        metricas_fortes=inteiro(obter(d, "metricas_fortes", c), f"{c}.metricas_fortes", minimo=1),
        metricas=metricas,
    )


def config_de_dict(dados: Any, *, fonte: Path | None = None) -> ConfigVocal:
    dados = mapa(dados, "<raiz>")
    sem_chaves_extras(dados, {"versao", "praat", "segmentos", "pausas", "respiratorio",
                              "qualidade", "limiares", "classes", "baseline"}, "<raiz>")
    return ConfigVocal(
        versao=texto(obter(dados, "versao", "<raiz>"), "versao"),
        praat=_praat(obter(dados, "praat", "<raiz>")),
        segmentos=_segmentos(obter(dados, "segmentos", "<raiz>")),
        pausas=_pausas(obter(dados, "pausas", "<raiz>")),
        respiratorio=_respiratorio(obter(dados, "respiratorio", "<raiz>")),
        qualidade=_qualidade(obter(dados, "qualidade", "<raiz>")),
        limiares=_limiares(obter(dados, "limiares", "<raiz>")),
        regras=_classes(obter(dados, "classes", "<raiz>")),
        baseline=_baseline(obter(dados, "baseline", "<raiz>")),
        fonte=fonte,
    )


def carregar_config(caminho: str | Path = CAMINHO_PADRAO) -> ConfigVocal:
    caminho = Path(caminho)
    return config_de_dict(ler_yaml(caminho), fonte=caminho)
