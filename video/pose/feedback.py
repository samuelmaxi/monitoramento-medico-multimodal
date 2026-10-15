"""Feedback em linguagem natural sobre um vídeo já processado.

Lê os ângulos por quadro (``compute_angles``) e devolve frases em pt-BR
separadas em pontos positivos, negativos e de alerta, além de um resumo.

As regras são determinísticas e explicáveis: cada frase cita o valor medido, o
limiar e a duração. Os limiares vêm de ``feedback`` no ``config.yaml`` e valem
para qualquer exercício; não há faixa por procedimento (isso é da US08).

Limitações que as frases não escondem: os ângulos são projeções 2D, então
dependem da posição da câmera, e a "inclinação do tronco" mede o quanto o
tronco sai da vertical, não a curvatura da coluna.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .angles import ASYMMETRIES, JOINT_ANGLES, TRUNK_COLUMN
from .config import FeedbackConfig

AVISO = (
    "Análise automática a partir de vídeo 2D, com critérios gerais que não consideram o "
    "exercício realizado. Serve de apoio e não substitui a avaliação de um profissional."
)

_PARES = {
    "assimetria_joelho": "os joelhos",
    "assimetria_quadril": "os quadris",
    "assimetria_ombro": "os ombros",
    "assimetria_cotovelo": "os cotovelos",
}
_JOELHOS = ("joelho_esquerdo", "joelho_direito")


@dataclass
class Feedback:
    resumo: str = ""
    pontos_positivos: list[str] = field(default_factory=list)
    pontos_negativos: list[str] = field(default_factory=list)
    pontos_de_alerta: list[str] = field(default_factory=list)
    aviso: str = AVISO


@dataclass(frozen=True)
class _Desvio:
    """Períodos contínuos em que uma medida ficou acima do limiar."""

    total_s: float
    pico: float
    pico_s: float


def _n(valor: float, casas: int = 0) -> str:
    return f"{valor:.{casas}f}".replace(".", ",")


def _desvio(ts_ms: pd.Series, valores: pd.Series, limiar: float, periodo_ms: float, min_s: float) -> _Desvio | None:
    """Soma os trechos em que ``valores > limiar`` sem interrupção por ao menos ``min_s``.

    Quadro sem medida (NaN) interrompe o trecho: não dá para afirmar que o desvio continuou.
    """
    acima = (valores > limiar).to_numpy()
    tempos = ts_ms.to_numpy()
    total_ms = 0.0
    indices: list[int] = []
    inicio = None
    for i in range(len(acima) + 1):
        if i < len(acima) and acima[i]:
            inicio = i if inicio is None else inicio
            continue
        if inicio is not None:
            duracao = tempos[i - 1] - tempos[inicio] + periodo_ms
            if duracao >= min_s * 1000:
                total_ms += duracao
                indices.extend(range(inicio, i))
            inicio = None
    if not indices:
        return None
    trecho = valores.iloc[indices]
    return _Desvio(total_ms / 1000, float(trecho.max()), float(ts_ms.loc[trecho.idxmax()]) / 1000)


def _quando(d: _Desvio, unidade: str = "°") -> str:
    return f"por {_n(d.total_s, 1)} s (pico de {_n(d.pico)}{unidade} aos {_n(d.pico_s)} s do vídeo)"


def build_feedback(
    angles: pd.DataFrame,
    *,
    frames_com_esqueleto: int,
    pct_frames_com_esqueleto: float,
    fps_processado: float,
    duracao_s: float,
    cfg: FeedbackConfig,
) -> Feedback:
    fb = Feedback()
    pct = _n(pct_frames_com_esqueleto, 1)

    if frames_com_esqueleto == 0:
        fb.pontos_de_alerta.append(
            "Nenhuma pessoa foi detectada no vídeo, então não há postura para avaliar. "
            "Grave de novo com o corpo inteiro visível e boa iluminação."
        )
        fb.resumo = fb.pontos_de_alerta[0]
        return fb

    if pct_frames_com_esqueleto < cfg.min_detection_pct:
        fb.pontos_de_alerta.append(
            f"A pessoa só foi detectada em {pct}% dos quadros, então o resultado abaixo é pouco "
            "confiável. Grave de novo com o corpo inteiro visível e boa iluminação."
        )
    elif pct_frames_com_esqueleto < cfg.good_detection_pct:
        fb.pontos_negativos.append(
            f"A pessoa foi detectada em {pct}% dos quadros; parte do vídeo ficou sem análise."
        )
    else:
        fb.pontos_positivos.append(
            f"A pessoa ficou visível em {pct}% dos quadros, uma boa base para a análise."
        )

    pouco_visiveis = [
        nome.replace("_", " ") for nome in JOINT_ANGLES
        if 100.0 * angles[nome].notna().sum() / frames_com_esqueleto < cfg.min_joint_visibility_pct
    ]
    if pouco_visiveis:
        fb.pontos_de_alerta.append(
            f"Pouca visibilidade de: {', '.join(pouco_visiveis)} (ângulo medido em menos de "
            f"{_n(cfg.min_joint_visibility_pct)}% dos quadros com a pessoa). O que depende dessas "
            "articulações pode ter passado sem análise; ajuste o enquadramento da câmera."
        )

    periodo_ms = 1000.0 / fps_processado
    ts = angles["timestamp_ms"]

    def desvio(valores: pd.Series, limiar: float) -> _Desvio | None:
        return _desvio(ts, valores, limiar, periodo_ms, cfg.min_duration_s)

    # Tronco
    tronco = angles[TRUNK_COLUMN]
    if tronco.notna().any():
        grave = desvio(tronco, cfg.trunk_alert_deg)
        moderado = desvio(tronco, cfg.trunk_warn_deg)
        if grave:
            fb.pontos_de_alerta.append(
                f"O tronco ficou inclinado mais de {_n(cfg.trunk_alert_deg)}° em relação à vertical "
                f"{_quando(grave)}. Uma inclinação tão acentuada pode indicar perda de equilíbrio ou "
                "sobrecarga na coluna lombar; verifique a pessoa e interrompa o exercício se for o caso."
            )
        elif moderado:
            fb.pontos_negativos.append(
                f"O tronco ficou inclinado mais de {_n(cfg.trunk_warn_deg)}° em relação à vertical "
                f"{_quando(moderado)}. Manter essa inclinação aumenta a carga na coluna lombar; "
                "peça para deixar o tronco mais ereto."
            )
        else:
            fb.pontos_positivos.append(
                f"O tronco se manteve estável, com inclinação média de {_n(tronco.mean())}° e sem "
                f"períodos acima de {_n(cfg.trunk_warn_deg)}°."
            )

    # Assimetria esquerdo x direito
    simetricos: list[str] = []
    for coluna in ASYMMETRIES:
        valores = angles[coluna]
        if not valores.notna().any():
            continue
        d = desvio(valores, cfg.asymmetry_warn_deg)
        if d:
            fb.pontos_negativos.append(
                f"A diferença entre {_PARES[coluna]} esquerdo e direito passou de "
                f"{_n(cfg.asymmetry_warn_deg)}° {_quando(d)}. Isso pode indicar que um lado está "
                "compensando o outro; peça para distribuir o movimento igualmente entre os dois lados."
            )
        else:
            simetricos.append(_PARES[coluna].removeprefix("os "))
    if simetricos:
        fb.pontos_positivos.append(
            f"Movimento simétrico entre os lados esquerdo e direito em: {', '.join(simetricos)}."
        )

    # Flexão do joelho
    dentro_da_faixa: list[float] = []
    for coluna in _JOELHOS:
        flexao = 180.0 - angles[coluna]
        if not flexao.notna().any():
            continue
        d = desvio(flexao, cfg.knee_max_flexion_deg)
        if d:
            fb.pontos_negativos.append(
                f"O {coluna.replace('_', ' ')} ficou flexionado além de {_n(cfg.knee_max_flexion_deg)}° "
                f"{_quando(d)}. Flexão profunda mantida aumenta a carga na articulação; "
                "peça para reduzir a amplitude."
            )
        else:
            dentro_da_faixa.append(float(flexao.max()))
    if len(dentro_da_faixa) == len(_JOELHOS):
        fb.pontos_positivos.append(
            f"Os joelhos ficaram dentro da faixa de flexão (máxima de {_n(max(dentro_da_faixa))}°, "
            f"limite de {_n(cfg.knee_max_flexion_deg)}°)."
        )

    fb.resumo = _resumo(fb, duracao_s)
    return fb


def _contagem(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _resumo(fb: Feedback, duracao_s: float) -> str:
    duracao = f"{_n(duracao_s)} s de vídeo"
    if not fb.pontos_de_alerta and not fb.pontos_negativos:
        return " ".join([f"Nenhum desvio postural encontrado em {duracao}.", *fb.pontos_positivos])
    partes = []
    if fb.pontos_de_alerta:
        partes.append(_contagem(len(fb.pontos_de_alerta), "ponto de alerta", "pontos de alerta"))
    if fb.pontos_negativos:
        partes.append(_contagem(len(fb.pontos_negativos), "ponto negativo", "pontos negativos"))
    abertura = f"Em {duracao} a análise encontrou {' e '.join(partes)}."
    return " ".join([abertura, *fb.pontos_de_alerta, *fb.pontos_negativos])
