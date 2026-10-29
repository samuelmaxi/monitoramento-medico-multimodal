"""Detector de anomalias em sinais vitais, processando uma medição por vez (streaming).

Para cada medição: limpeza → NEWS2 com os últimos valores válidos → camada de ML
não supervisionada sobre a baseline do próprio paciente:

- ``desvio_baseline`` (mudança súbita): z-score robusto (mediana/MAD) contra a
  janela de baseline. Valores em desvio não entram na baseline, que fica
  congelada até o episódio acabar ou a janela expirar.
- ``tendencia_deterioracao``: inclinação da regressão linear na janela de
  tendência, medida em unidades da escala da baseline.

Cada episódio emite um evento só; o próximo sai depois que o sinal volta ao normal.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from contratos import EventoAchado, Modalidade, Severidade, agora_utc, severidade_sugerida

from . import news2
from .limpeza import VALIDADE, plausivel

MODEL_VERSION = "anomalias-sinais@0.1.0"
SINAIS_CONTINUOS = ("fc", "pas", "pad", "spo2", "fr", "temp")
UNIDADE = {"fc": "bpm", "pas": "mmhg", "pad": "mmhg", "spo2": "pct", "fr": "irpm", "temp": "c"}
# Escala mínima da baseline: evita z enorme quando o sinal fica constante (ex.: SpO2 98, 98, 98).
ESCALA_MINIMA = {"fc": 4.0, "pas": 6.0, "pad": 4.0, "spo2": 1.0, "fr": 2.0, "temp": 0.3}
_NIVEL = {None: 0, news2.URGENTE: 1, news2.EMERGENCIA: 2}


@dataclass(frozen=True)
class Medicao:
    paciente: str  # pseudônimo pt_<hex>
    instante: datetime  # UTC
    sinal: str
    valor: float


@dataclass(frozen=True)
class ConfigDetector:
    janela_baseline: timedelta = timedelta(hours=12)
    min_pontos_baseline: int = 6
    # Limiares escolhidos por busca em grade no conjunto de ajuste (scripts/avaliar_us15.py).
    limiar_z: float = 2.5
    medicoes_consecutivas: int = 2
    janela_tendencia: timedelta = timedelta(hours=6)
    min_pontos_tendencia: int = 4
    limiar_tendencia: float = 2.5


@dataclass
class _EstadoSinal:
    baseline: deque = field(default_factory=deque)  # (instante, valor) aceitos como normais
    recentes: deque = field(default_factory=deque)  # (instante, valor) de todas as medições
    consecutivos: int = 0
    em_desvio: bool = False
    em_tendencia: bool = False


@dataclass
class _EstadoPaciente:
    ultimos: dict = field(default_factory=dict)  # sinal → (instante, valor)
    sinais: dict = field(default_factory=dict)  # sinal → _EstadoSinal
    nivel_news2: str | None = None


class DetectorSinais:
    def __init__(self, config: ConfigDetector | None = None, *, sintetico: bool = False) -> None:
        self.config = config or ConfigDetector()
        self.sintetico = sintetico
        self.artefatos: Counter[str] = Counter()
        self._pacientes: dict[str, _EstadoPaciente] = {}

    def processar(self, medicao: Medicao) -> list[EventoAchado]:
        if medicao.sinal not in VALIDADE:
            raise ValueError(f"sinal desconhecido: {medicao.sinal}")
        if not plausivel(medicao.sinal, medicao.valor):
            self.artefatos[medicao.sinal] += 1
            return []
        paciente = self._pacientes.setdefault(medicao.paciente, _EstadoPaciente())
        paciente.ultimos[medicao.sinal] = (medicao.instante, medicao.valor)
        eventos = self._news2(medicao, paciente)
        if medicao.sinal in SINAIS_CONTINUOS:
            estado = paciente.sinais.setdefault(medicao.sinal, _EstadoSinal())
            eventos += self._baseline(medicao, estado)
        return eventos

    # ------------------------------------------------------------------ NEWS2

    def _news2(self, medicao: Medicao, paciente: _EstadoPaciente) -> list[EventoAchado]:
        valores = {
            sinal: valor
            for sinal, (instante, valor) in paciente.ultimos.items()
            if medicao.instante - instante <= VALIDADE[sinal]
        }
        resultado = news2.calcular(valores)
        gatilho = resultado.gatilho
        subiu = _NIVEL[gatilho] > _NIVEL[paciente.nivel_news2]
        paciente.nivel_news2 = gatilho
        if not subiu:
            return []
        inicio = min(paciente.ultimos[s][0] for s in resultado.pontos)
        features = {
            f"{s}_{UNIDADE[s]}" if s in UNIDADE else s: valores[s] for s in resultado.pontos
        }
        features |= {f"news2_{s}": p for s, p in resultado.pontos.items()}
        features |= {
            "news2_total": resultado.total,
            "news2_parcial": bool(resultado.ausentes),
            "news2_ausentes": ",".join(resultado.ausentes),
        }
        resumo = ", ".join(f"{s} {valores[s]:g}" for s, p in resultado.pontos.items() if p)
        acao = "emergência" if gatilho == news2.EMERGENCIA else "resposta urgente"
        return [
            self._evento(
                medicao,
                inicio,
                gatilho,
                score=min(1.0, resultado.total / news2.TOTAL_MAXIMO),
                severidade=Severidade.ALTA if gatilho == news2.EMERGENCIA else Severidade.MEDIA,
                resumo=f"NEWS2 = {resultado.total} ({resumo}): {acao}",
                features=features,
                limiares={
                    "news2_urgente_total": news2.URGENTE_TOTAL,
                    "news2_emergencia_total": news2.EMERGENCIA_TOTAL,
                    "news2_parametro_unico": news2.PARAMETRO_UNICO,
                },
                modelos={"news2": "RCP-2017"},
            )
        ]

    # ------------------------------------------------------------------ ML

    def _baseline(self, medicao: Medicao, estado: _EstadoSinal) -> list[EventoAchado]:
        cfg = self.config
        agora, valor = medicao.instante, medicao.valor
        _expirar(estado.baseline, agora - cfg.janela_baseline)
        _expirar(estado.recentes, agora - cfg.janela_tendencia)
        estado.recentes.append((agora, valor))
        if len(estado.baseline) < cfg.min_pontos_baseline:
            estado.baseline.append((agora, valor))  # aquecimento
            return []

        historico = np.array([v for _, v in estado.baseline])
        mediana = float(np.median(historico))
        escala = max(
            1.4826 * float(np.median(np.abs(historico - mediana))), ESCALA_MINIMA[medicao.sinal]
        )
        z = (valor - mediana) / escala
        comum = {
            "sinal": medicao.sinal,
            "valor": valor,
            "baseline_mediana": round(mediana, 2),
            "baseline_escala": round(escala, 2),
            "baseline_pontos": len(historico),
        }
        inicio_baseline = estado.baseline[0][0]
        eventos = []

        if abs(z) > cfg.limiar_z:
            estado.consecutivos += 1
        else:
            estado.consecutivos = 0
            estado.em_desvio = False
            estado.baseline.append((agora, valor))
        if estado.consecutivos >= cfg.medicoes_consecutivas and not estado.em_desvio:
            estado.em_desvio = True
            score = min(1.0, abs(z) / 10)
            eventos.append(
                self._evento(
                    medicao,
                    inicio_baseline,
                    "desvio_baseline",
                    score=score,
                    severidade=severidade_sugerida(score),
                    resumo=f"{medicao.sinal} {valor:g} desvia da baseline do paciente "
                    f"(mediana {mediana:g}, z = {z:+.1f})",
                    features=comum | {"z": round(z, 2)},
                    limiares={
                        "limiar_z": cfg.limiar_z,
                        "medicoes_consecutivas": cfg.medicoes_consecutivas,
                    },
                    modelos={"zscore_robusto": "mediana-MAD"},
                )
            )

        razao = _variacao_linear(estado.recentes, cfg) / escala
        if abs(razao) > cfg.limiar_tendencia and not estado.em_tendencia:
            estado.em_tendencia = True
            score = min(1.0, abs(razao) / 10)
            horas = cfg.janela_tendencia.total_seconds() / 3600
            eventos.append(
                self._evento(
                    medicao,
                    estado.recentes[0][0],
                    "tendencia_deterioracao",
                    score=score,
                    severidade=severidade_sugerida(score),
                    resumo=f"Tendência de {'alta' if razao > 0 else 'queda'} em {medicao.sinal} "
                    f"nas últimas {horas:g} h ({razao:+.1f} escalas da baseline)",
                    features=comum | {"variacao_em_escalas": round(razao, 2)},
                    limiares={"limiar_tendencia": cfg.limiar_tendencia},
                    modelos={"regressao_linear": "numpy.polyfit"},
                )
            )
        elif abs(razao) < cfg.limiar_tendencia / 2:
            estado.em_tendencia = False
        return eventos

    def _evento(
        self, medicao, inicio, tipo, *, score, severidade, resumo, features, limiares, modelos
    ):
        return EventoAchado.criar(
            patient_id=medicao.paciente,
            modality=Modalidade.SINAIS_VITAIS,
            event_type=tipo,
            timestamp=medicao.instante,
            ingested_at=agora_utc(),
            window={"start": inicio, "end": medicao.instante},
            score=score,
            severity=severidade,
            evidence={
                "summary": resumo,
                "features": features,
                "thresholds": limiares,
                "models": modelos,
            },
            model_version=MODEL_VERSION,
            is_synthetic=self.sintetico,
        )


def _expirar(janela: deque, limite: datetime) -> None:
    while janela and janela[0][0] < limite:
        janela.popleft()


def _variacao_linear(recentes: deque, cfg: ConfigDetector) -> float:
    """Variação prevista pela reta ajustada ao longo da janela; 0 se há pouco dado."""
    if len(recentes) < cfg.min_pontos_tendencia:
        return 0.0
    horas = np.array([(t - recentes[0][0]).total_seconds() / 3600 for t, _ in recentes])
    if horas[-1] < cfg.janela_tendencia.total_seconds() / 3600 / 2:
        return 0.0
    inclinacao = np.polyfit(horas, [v for _, v in recentes], 1)[0]
    return float(inclinacao * horas[-1])
