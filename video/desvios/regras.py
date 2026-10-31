"""Regras de desvio sobre a pose da US06 e orquestração da análise de um vídeo.

Cada regra lê uma série por quadro e devolve :class:`~video.desvios.modelos.Desvio`
só para trechos que persistiram pela janela do perfil:

- ``faixas``: ângulo fora de ``[min, max]``                  → ``postura_fora_da_faixa``
- ``repeticoes``: repetição que não chegou ao alvo           → ``postura_fora_da_faixa``
- ``assimetrias``: ``|esq - dir|`` acima do máximo           → ``assimetria_movimento``
- ``queda``: quadril desce rápido e não volta                → ``mudanca_brusca_ou_queda``
- ``mudanca_brusca``: tronco gira rápido e fica na posição   → ``mudanca_brusca_ou_queda``

A presença indevida em área crítica vem dos eventos da US07, em
:mod:`video.desvios.areas`. :func:`analisar` junta as duas fontes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import pandas as pd

from contratos import EventoAchado
from video.pose.angles import TRUNK_COLUMN, compute_angles, hip_center
from video.pose.sequence import PoseSequence

from .areas import desvios_de_areas
from .config import ConfigDesvios, Faixa, Geral, Perfil, RegraRepeticoes, RegraSalto
from .modelos import Desvio, Repeticao, ResultadoAnalise, ResumoRepeticoes
from .sinais import interpolar_lacunas, periodo_amostral, saltos_mantidos, trechos

POSTURA = "postura_fora_da_faixa"
ASSIMETRIA = "assimetria_movimento"
QUEDA = "mudanca_brusca_ou_queda"

_PARES = {
    "assimetria_joelho": "os joelhos",
    "assimetria_quadril": "os quadris",
    "assimetria_ombro": "os ombros",
    "assimetria_cotovelo": "os cotovelos",
}


def _n(valor: float, casas: int = 0) -> str:
    return f"{valor:.{casas}f}".replace(".", ",")


def _rotulo(coluna: str) -> str:
    if coluna == TRUNK_COLUMN:
        return "A inclinação do tronco"
    if coluna in _PARES:
        return f"A diferença entre {_PARES[coluna]} esquerdo e direito"
    articulacao, lado = coluna.split("_")
    return f"O ângulo do {articulacao} {lado}"


def _score(excesso: float, saturacao: float) -> float:
    """0,5 ao passar o limiar, 1,0 quando o excesso chega à saturação."""
    return round(0.5 + 0.5 * min(1.0, max(0.0, excesso) / saturacao), 4)


# --------------------------------------------------------------------------- #
# Faixas e assimetrias
# --------------------------------------------------------------------------- #


def desvios_de_faixa(
    tempos_s: np.ndarray,
    frames: np.ndarray,
    valores: np.ndarray,
    faixa: Faixa,
    geral: Geral,
    event_type: str,
) -> list[Desvio]:
    """Trechos em que a medida ficou fora de ``[min, max]`` por ``janela_s`` ou mais."""
    excesso = np.full(len(valores), np.nan)
    with np.errstate(invalid="ignore"):
        if faixa.max is not None:
            excesso = np.fmax(excesso, valores - faixa.max)
        if faixa.min is not None:
            excesso = np.fmax(excesso, faixa.min - valores)
        fora = excesso > 0
    desvios = []
    for ini, fim in trechos(
        tempos_s, fora, janela_s=faixa.janela_s, lacuna_max_s=geral.lacuna_max_s
    ):
        pico = ini + int(np.nanargmax(excesso[ini : fim + 1]))
        acima = faixa.max is not None and valores[pico] > faixa.max
        limiar = faixa.max if acima else faixa.min
        duracao = tempos_s[fim] - tempos_s[ini] + periodo_amostral(tempos_s)
        resumo = (
            f"{_rotulo(faixa.coluna)} ficou {'acima' if acima else 'abaixo'} de {_n(limiar)}° "
            f"por {_n(duracao, 1)} s (pico de {_n(valores[pico])}° aos "
            f"{_n(tempos_s[pico], 1)} s do vídeo)."
        )
        desvios.append(
            Desvio(
                event_type=event_type,
                regra=f"{'assimetria' if event_type == ASSIMETRIA else 'faixa'}.{faixa.coluna}",
                severidade=faixa.severidade,
                score=_score(excesso[pico], geral.score.saturacao_graus),
                inicio_s=float(tempos_s[ini]),
                fim_s=float(tempos_s[fim] + periodo_amostral(tempos_s)),
                pico_s=float(tempos_s[pico]),
                frame_idx=int(frames[pico]),
                medida=faixa.coluna,
                valor=round(float(valores[pico]), 2),
                limiar=float(limiar),
                unidade="graus",
                janela_s=faixa.janela_s,
                resumo=resumo,
                features={
                    "faixa_min": faixa.min,
                    "faixa_max": faixa.max,
                    "excesso_graus": round(float(excesso[pico]), 2),
                },
            )
        )
    return desvios


# --------------------------------------------------------------------------- #
# Repetições
# --------------------------------------------------------------------------- #


def _fase_de_pico(x: np.ndarray, ini: int, fim: int, pico: int, largura: float) -> np.ndarray:
    """Índices da repetição em que o ângulo principal está a até ``largura`` graus do pico."""
    trecho = np.arange(ini, fim + 1)
    with np.errstate(invalid="ignore"):
        return trecho[np.abs(x[trecho] - x[pico]) <= largura]


def repeticoes(
    tempos_s: np.ndarray,
    frames: np.ndarray,
    angulos: pd.DataFrame,
    regra: RegraRepeticoes,
    geral: Geral,
) -> tuple[ResumoRepeticoes, list[Desvio]]:
    """Separa as repetições pelo ângulo principal e julga cada uma.

    A repetição é o trecho em que o ângulo sai do repouso (acima de ``repouso``
    no sentido "sobe", abaixo no "desce") por ``duracao_min_s`` ou mais. Cada
    repetição vira ``postura_fora_da_faixa`` se o pico não alcança ``alvo``
    (amplitude insuficiente) ou, com ``referencia``, se algum ângulo na fase de
    pico se afasta da média das repetições de referência mais que a tolerância.
    As repetições de referência não são julgadas.
    """
    sinal = 1.0 if regra.sentido == "sobe" else -1.0
    x = interpolar_lacunas(
        angulos[regra.angulo].to_numpy(dtype=float), tempos_s, geral.lacuna_max_s
    )
    with np.errstate(invalid="ignore"):
        ativo = sinal * (x - regra.repouso) > 0
    periodo = periodo_amostral(tempos_s)
    ref = regra.referencia
    # Trecho que encosta no início ou no fim do vídeo é repetição cortada: não dá
    # para saber até onde ela iria, então não conta nem é julgada.
    trechos_ativos = [
        (ini, fim)
        for ini, fim in trechos(
            tempos_s, ativo, janela_s=regra.duracao_min_s, lacuna_max_s=geral.lacuna_max_s
        )
        if ini > 0 and fim < len(x) - 1
    ]
    reps: list[Repeticao] = []
    for numero, (ini, fim) in enumerate(trechos_ativos, start=1):
        pico = ini + int(np.nanargmax(sinal * x[ini : fim + 1]))
        medidas = {}
        if ref is not None:
            fase = _fase_de_pico(x, ini, fim, pico, ref.fase_pico_graus)
            for coluna in ref.tolerancias:
                serie = angulos[coluna].to_numpy(dtype=float)[fase]
                valor = x[pico] if coluna == regra.angulo else np.nanmedian(serie)
                medidas[coluna] = round(float(valor), 2)
        reps.append(
            Repeticao(
                numero=numero,
                inicio_s=float(tempos_s[ini]),
                fim_s=float(tempos_s[fim] + periodo),
                pico_s=float(tempos_s[pico]),
                pico=float(x[pico]),
                atingiu_alvo=None
                if regra.alvo is None
                else bool(sinal * (x[pico] - regra.alvo) >= 0),
                frame_pico=int(frames[pico]),
                medidas=medidas,
                referencia=ref is not None and numero <= ref.repeticoes,
            )
        )

    padrao = None
    if ref is not None and len(reps) > ref.repeticoes:
        base = [r for r in reps if r.referencia]
        padrao = {
            coluna: round(float(np.nanmean([r.medidas[coluna] for r in base])), 2)
            for coluna in ref.tolerancias
        }

    desvios = []
    for rep in reps:
        if rep.atingiu_alvo is False:
            desvios.append(_desvio_de_amplitude(rep, regra, geral))
        elif padrao is not None and not rep.referencia:
            desvio = _desvio_de_referencia(rep, padrao, regra, geral)
            if desvio is not None:
                desvios.append(desvio)
    resumo = ResumoRepeticoes(
        angulo=regra.angulo,
        alvo=regra.alvo,
        repeticoes=tuple(reps),
        esperadas=regra.esperadas,
        referencia=padrao,
    )
    return resumo, desvios


def _desvio_de_amplitude(rep: Repeticao, regra: RegraRepeticoes, geral: Geral) -> Desvio:
    deficit = abs(regra.alvo - rep.pico)
    relacao = "abaixo" if regra.sentido == "sobe" else "acima"
    return Desvio(
        event_type=POSTURA,
        regra=f"repeticao.{regra.angulo}",
        severidade=regra.severidade,
        score=_score(deficit, geral.score.saturacao_graus),
        inicio_s=rep.inicio_s,
        fim_s=rep.fim_s,
        pico_s=rep.pico_s,
        frame_idx=rep.frame_pico,
        medida=regra.angulo,
        valor=round(rep.pico, 2),
        limiar=regra.alvo,
        unidade="graus",
        janela_s=regra.duracao_min_s,
        resumo=(
            f"Na repetição {rep.numero} ({_n(rep.inicio_s, 1)} a {_n(rep.fim_s, 1)} s) "
            f"{_rotulo(regra.angulo).lower()} chegou a {_n(rep.pico)}°, {relacao} do "
            f"alvo de {_n(regra.alvo)}°: amplitude insuficiente."
        ),
        features={
            "repeticao": rep.numero,
            "deficit_graus": round(float(deficit), 2),
            "repouso": regra.repouso,
        },
    )


def _desvio_de_referencia(
    rep: Repeticao, padrao: dict[str, float], regra: RegraRepeticoes, geral: Geral
) -> Desvio | None:
    ref = regra.referencia
    diferencas = {c: rep.medidas[c] - padrao[c] for c in ref.tolerancias}
    fora = {c: d for c, d in diferencas.items() if not np.isnan(d) and abs(d) > ref.tolerancias[c]}
    if not fora:
        return None
    pior = max(fora, key=lambda c: abs(fora[c]) - ref.tolerancias[c])
    detalhes = "; ".join(
        f"{_rotulo(c).lower().removeprefix('o ').removeprefix('a ')} em {_n(rep.medidas[c])}° "
        f"(referência {_n(padrao[c])}°, tolerância ±{_n(ref.tolerancias[c])}°)"
        for c in fora
    )
    return Desvio(
        event_type=POSTURA,
        regra="repeticao.referencia",
        severidade=regra.severidade,
        score=_score(abs(fora[pior]) - ref.tolerancias[pior], geral.score.saturacao_graus),
        inicio_s=rep.inicio_s,
        fim_s=rep.fim_s,
        pico_s=rep.pico_s,
        frame_idx=rep.frame_pico,
        medida=pior,
        valor=rep.medidas[pior],
        limiar=ref.tolerancias[pior],
        unidade="graus",
        janela_s=regra.duracao_min_s,
        resumo=(
            f"Na repetição {rep.numero} ({_n(rep.inicio_s, 1)} a {_n(rep.fim_s, 1)} s) o "
            f"movimento saiu do padrão das {ref.repeticoes} repetições de referência: {detalhes}."
        ),
        features={
            "repeticao": rep.numero,
            "repeticoes_referencia": ref.repeticoes,
            **{f"referencia_{c}": padrao[c] for c in ref.tolerancias},
            **{f"diferenca_{c}": round(float(d), 2) for c, d in diferencas.items()},
        },
    )


# --------------------------------------------------------------------------- #
# Queda e mudança brusca
# --------------------------------------------------------------------------- #


def serie_quadril(seq: PoseSequence) -> tuple[np.ndarray, np.ndarray, float]:
    """Altura do centro do quadril por quadro, em comprimentos de tronco.

    O tronco (pescoço→quadril) é a escala porque não depende da distância da
    câmera nem da resolução, e muda pouco quando a pessoa deita. y cresce para
    baixo, então queda é a série subindo. Devolve (tempos_s, y, tronco_px).
    """
    tempos = np.array([f.timestamp_ms / 1000 for f in seq.frames], dtype=float)
    y = np.full(len(seq.frames), np.nan)
    tronco = np.full(len(seq.frames), np.nan)
    for i, frame in enumerate(seq.frames):
        quadril = hip_center(frame.keypoints)
        if quadril is None:
            continue
        y[i] = quadril[1]
        pescoco = frame.keypoints.get("Neck")
        if pescoco is not None:
            tronco[i] = math.hypot(pescoco[0] - quadril[0], pescoco[1] - quadril[1])
    escala = float(np.nanmedian(tronco)) if np.any(~np.isnan(tronco)) else math.nan
    return tempos, y / escala, escala


def _desvio_de_salto(
    salto,
    tempos_s: np.ndarray,
    frames: np.ndarray,
    regra: RegraSalto,
    *,
    subtipo: str,
    medida: str,
    unidade: str,
    saturacao: float,
    resumo: str,
    features: dict,
) -> Desvio:
    periodo = periodo_amostral(tempos_s)
    return Desvio(
        event_type=QUEDA,
        regra=subtipo,
        severidade=regra.severidade,
        score=_score(abs(salto.variacao) - regra.limiar, saturacao),
        inicio_s=float(tempos_s[salto.ref]),
        fim_s=float(tempos_s[salto.fim] + periodo),
        pico_s=float(tempos_s[salto.pico]),
        frame_idx=int(frames[salto.pico]),
        medida=medida,
        valor=round(salto.variacao, 3),
        limiar=regra.limiar,
        unidade=unidade,
        janela_s=regra.janela_s,
        resumo=resumo,
        features={
            "subtipo": subtipo,
            "tempo_do_salto_s": round(float(tempos_s[salto.impacto] - tempos_s[salto.ref]), 2),
            "tempo_max_s": regra.tempo_max_s,
            **features,
        },
    )


def desvios_de_queda(seq: PoseSequence, regra: RegraSalto, geral: Geral) -> list[Desvio]:
    tempos, y, escala = serie_quadril(seq)
    if math.isnan(escala):
        return []
    frames = np.array([f.frame_idx for f in seq.frames])
    y = interpolar_lacunas(y, tempos, geral.lacuna_max_s)
    desvios = []
    for salto in saltos_mantidos(
        tempos,
        y,
        limiar=regra.limiar,
        tempo_max_s=regra.tempo_max_s,
        janela_s=regra.janela_s,
        lacuna_max_s=geral.lacuna_max_s,
        fracao_permanencia=geral.fracao_permanencia,
        sinal=1,
        ausencia_mantem=True,
    ):
        tempo = tempos[salto.impacto] - tempos[salto.ref]
        embaixo = tempos[salto.fim] - tempos[salto.impacto] + periodo_amostral(tempos)
        desvios.append(
            _desvio_de_salto(
                salto,
                tempos,
                frames,
                regra,
                subtipo="queda",
                medida="descida_quadril",
                unidade="comprimentos de tronco",
                saturacao=geral.score.saturacao_torsos,
                resumo=(
                    f"Possível queda aos {_n(tempos[salto.impacto], 1)} s: o quadril desceu "
                    f"{_n(salto.variacao, 1)} comprimentos de tronco em {_n(tempo, 1)} s e "
                    f"ficou embaixo por {_n(embaixo, 1)} s."
                ),
                features={"tronco_px": round(escala, 1)},
            )
        )
    return desvios


def desvios_de_mudanca_brusca(
    tempos_s: np.ndarray,
    frames: np.ndarray,
    tronco: np.ndarray,
    regra: RegraSalto,
    geral: Geral,
) -> list[Desvio]:
    x = interpolar_lacunas(tronco, tempos_s, geral.lacuna_max_s)
    desvios = []
    for sinal, direcao in ((1, "deitou ou curvou"), (-1, "levantou o tronco")):
        for salto in saltos_mantidos(
            tempos_s,
            x,
            limiar=regra.limiar,
            tempo_max_s=regra.tempo_max_s,
            janela_s=regra.janela_s,
            lacuna_max_s=geral.lacuna_max_s,
            fracao_permanencia=geral.fracao_permanencia,
            sinal=sinal,
        ):
            tempo = tempos_s[salto.impacto] - tempos_s[salto.ref]
            desvios.append(
                _desvio_de_salto(
                    salto,
                    tempos_s,
                    frames,
                    regra,
                    subtipo="mudanca_brusca",
                    medida=TRUNK_COLUMN,
                    unidade="graus",
                    saturacao=geral.score.saturacao_graus,
                    resumo=(
                        f"Mudança brusca de posição aos {_n(tempos_s[salto.impacto], 1)} s: "
                        f"o tronco girou {_n(abs(salto.variacao))}° em {_n(tempo, 1)} s "
                        f"({direcao}) e se manteve."
                    ),
                    features={"direcao": direcao},
                )
            )
    return desvios


def _sobrepoe(a: Desvio, b: Desvio) -> bool:
    return a.inicio_s < b.fim_s and b.inicio_s < a.fim_s


# --------------------------------------------------------------------------- #
# Análise de um vídeo
# --------------------------------------------------------------------------- #


def analisar(
    perfil: Perfil,
    cfg: ConfigDesvios,
    *,
    pose: PoseSequence | None = None,
    angulos: pd.DataFrame | None = None,
    eventos_areas: Sequence[EventoAchado] = (),
    fim_video_s: float | None = None,
) -> ResultadoAnalise:
    """Aplica as regras do perfil à pose (US06) e aos eventos de área (US07).

    Sem ``pose``, um perfil que também tem regras de área roda só elas e a
    análise fica marcada como não confiável (a postura não foi avaliada).

    Args:
        pose: sequência da US06. Obrigatória se o perfil só tem regras posturais.
        angulos: ângulos por quadro já calculados; sem eles, saem da ``pose``.
        eventos_areas: eventos ``entrada``/``saida_area_critica`` da US07.
        fim_video_s: duração do vídeo, para fechar uma presença sem ``saida``.
    """
    geral = cfg.geral
    desvios: list[Desvio] = []
    avisos: list[str] = []
    resumo_reps = None
    cobertura = None
    quadros = 0
    duracao = fim_video_s
    postura_avaliada = True

    if perfil.usa_pose and pose is None:
        if not perfil.areas:
            raise ValueError(
                f"o perfil '{perfil.id}' tem regras posturais e precisa da pose da US06"
            )
        postura_avaliada = False
        avisos.append("Sem pose da US06: só as regras de área crítica foram avaliadas.")
    elif perfil.usa_pose:
        if angulos is None:
            angulos = compute_angles(pose)
        tempos = angulos["timestamp_ms"].to_numpy(dtype=float) / 1000
        frames = angulos["frame_idx"].to_numpy()
        quadros = len(angulos)
        cobertura = round(100 * pose.detection_rate(), 2)
        if duracao is None and quadros:
            duracao = float(tempos[-1] + periodo_amostral(tempos))
        if cobertura < geral.cobertura_minima_pct:
            avisos.append(
                f"Pessoa detectada em só {_n(cobertura, 1)}% dos quadros (mínimo "
                f"{_n(geral.cobertura_minima_pct)}%): a ausência de desvios posturais não é "
                "conclusiva."
            )

        def serie(coluna: str) -> np.ndarray:
            return angulos[coluna].to_numpy(dtype=float)

        for faixa in perfil.faixas:
            desvios += desvios_de_faixa(tempos, frames, serie(faixa.coluna), faixa, geral, POSTURA)
        for faixa in perfil.assimetrias:
            desvios += desvios_de_faixa(
                tempos, frames, serie(faixa.coluna), faixa, geral, ASSIMETRIA
            )
        if perfil.repeticoes is not None:
            resumo_reps, de_reps = repeticoes(tempos, frames, angulos, perfil.repeticoes, geral)
            desvios += de_reps
            ref = perfil.repeticoes.referencia
            if ref is not None and resumo_reps.referencia is None:
                avisos.append(
                    f"Só {resumo_reps.contadas} repetições completas: a comparação com a "
                    f"referência precisa de mais de {ref.repeticoes}."
                )
            if resumo_reps.dentro_do_esperado is False:
                minimo, maximo = resumo_reps.esperadas
                avisos.append(
                    f"Foram contadas {resumo_reps.contadas} repetições; o padrão do perfil é de "
                    f"{minimo} a {maximo}."
                )
        quedas = desvios_de_queda(pose, perfil.queda, geral) if perfil.queda else []
        desvios += quedas
        if perfil.mudanca_brusca is not None:
            # A queda já explica o giro do tronco que acontece junto com ela.
            desvios += [
                d
                for d in desvios_de_mudanca_brusca(
                    tempos, frames, serie(TRUNK_COLUMN), perfil.mudanca_brusca, geral
                )
                if not any(_sobrepoe(d, q) for q in quedas)
            ]

    if perfil.areas:
        desvios += desvios_de_areas(eventos_areas, perfil.areas, geral, fim_video_s=duracao)

    return ResultadoAnalise(
        perfil=perfil.id,
        desvios=tuple(sorted(desvios, key=lambda d: (d.inicio_s, d.regra))),
        duracao_s=duracao,
        quadros=quadros,
        cobertura_pct=cobertura,
        confiavel=postura_avaliada
        and (cobertura is None or cobertura >= geral.cobertura_minima_pct),
        repeticoes=resumo_reps,
        avisos=tuple(avisos),
    )
