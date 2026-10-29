"""Detector em streaming: limpeza, gatilhos NEWS2 e camada de baseline."""

from datetime import UTC, datetime, timedelta

import pytest

from contratos import Severidade, pseudonimizar_paciente
from sinais import DetectorSinais, Medicao
from sinais.avaliacao import Matriz, injetar, montar_rotulos

from ..contratos.constantes import CHAVE_TESTE

T0 = datetime(2026, 9, 26, 12, tzinfo=UTC)


@pytest.fixture
def paciente():
    return pseudonimizar_paciente(10000032, chave=CHAVE_TESTE)


def serie(paciente, sinal, valores, inicio=T0):
    return [Medicao(paciente, inicio + timedelta(hours=h), sinal, v) for h, v in enumerate(valores)]


def rodar(detector, medicoes):
    return [e for m in medicoes for e in detector.processar(m)]


@pytest.mark.parametrize(("sinal", "valor"), [("spo2", 0), ("fc", 301), ("temp", float("nan"))])
def test_artefato_e_descartado(paciente, sinal, valor):
    detector = DetectorSinais()
    assert detector.processar(Medicao(paciente, T0, sinal, valor)) == []
    assert detector.artefatos[sinal] == 1


def test_news2_emite_so_na_escalada(paciente):
    detector = DetectorSinais()
    eventos = rodar(
        detector,
        [
            Medicao(paciente, T0, "fr", 25),  # 3 em parâmetro único → urgente
            Medicao(paciente, T0, "fr", 25),  # mesmo nível: não repete
            Medicao(paciente, T0, "fc", 135),
            Medicao(paciente, T0, "spo2", 91),  # total 9 → emergência
        ],
    )
    assert [(e.event_type, e.severity) for e in eventos] == [
        ("news2_resposta_urgente", Severidade.MEDIA),
        ("news2_emergencia", Severidade.ALTA),
    ]
    assert eventos[1].evidence.features["news2_total"] == 9
    assert eventos[1].evidence.features["news2_parcial"] is True


def test_valor_antigo_expira_do_news2(paciente):
    detector = DetectorSinais()
    assert rodar(detector, [Medicao(paciente, T0, "fr", 25)])
    # FR de 3 h atrás já não vale (validade 2 h): o nível cai e volta a subir com a nova FR.
    assert rodar(detector, [Medicao(paciente, T0 + timedelta(hours=3), "fc", 70)]) == []
    assert rodar(detector, [Medicao(paciente, T0 + timedelta(hours=3), "fr", 26)])


def test_desvio_baseline_em_degrau(paciente):
    normal = [72, 75, 70, 74, 73, 71, 76, 72, 74, 73]
    eventos = rodar(DetectorSinais(), serie(paciente, "fc", [*normal, 108, 110, 109]))
    desvios = [e for e in eventos if e.event_type == "desvio_baseline"]
    assert len(desvios) == 1  # um evento por episódio
    assert desvios[0].evidence.features["sinal"] == "fc"
    assert desvios[0].evidence.features["z"] > 2.5


def test_tendencia_em_rampa(paciente):
    normal = [97, 98, 97, 98, 97, 98, 97, 98]
    rampa = [97, 96, 95, 94, 93, 92]
    eventos = rodar(DetectorSinais(), serie(paciente, "spo2", normal + rampa))
    assert "tendencia_deterioracao" in {e.event_type for e in eventos}


def test_serie_estavel_sem_achados_de_ml(paciente):
    eventos = rodar(
        DetectorSinais(), serie(paciente, "fc", [72, 75, 70, 74, 73, 71, 76, 72, 74, 73] * 3)
    )
    assert eventos == []


def test_injecao_marca_e_altera_so_os_slots_positivos():
    import pandas as pd

    instantes = [T0 + timedelta(hours=h) for h in range(60)]
    df = pd.DataFrame(
        {
            "subject_id": 1,
            "instante": pd.to_datetime(instantes, utc=True),
            "sinal": "fc",
            "valor": 75.0,
        }
    )
    rotulos = montar_rotulos(df, semente=1)
    assert len(rotulos) and (rotulos.tipo != "").any()
    alterado = injetar(df, rotulos).valor != df.valor
    for r in rotulos.itertuples():
        no_slot = (df.instante >= r.inicio) & (df.instante < r.fim)
        assert alterado[no_slot].any() == (r.tipo != "")


def test_matriz():
    m = Matriz(vp=8, fp=2, fn=2, vn=8)
    assert (m.recall, m.precisao, m.f1) == (0.8, 0.8, pytest.approx(0.8))
