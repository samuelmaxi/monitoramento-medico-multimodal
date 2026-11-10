"""Fusão em tempo real no BarramentoLocal, até o motor de alertas (US18 + US19)."""

from __future__ import annotations

import asyncio

import pytest

from alertas import ConfigAlertas, construir
from contratos import BarramentoLocal, EmissorMemoria, Modalidade, Severidade
from fusao import FusaoMultimodal
from fusao.cenarios import cenario_dispneia

from .conftest import OUTRO, PACIENTE, RAIZ, T0, achado, instante


@pytest.fixture
def barramento(tmp_path):
    return BarramentoLocal(tmp_path / "eventos.jsonl")


def _da_fusao(eventos):
    return [e for e in eventos if e.modality is Modalidade.FUSAO]


def test_publica_risco_no_barramento(config, barramento):
    recebidos = EmissorMemoria()
    FusaoMultimodal(config).assinar(barramento)
    barramento.assinar("espiao", recebidos.emitir)

    barramento.emitir(achado(Modalidade.SINAIS_VITAIS, "news2_resposta_urgente", minuto=0))

    [risco] = _da_fusao(recebidos.eventos)
    assert risco.patient_id == PACIENTE
    assert risco.severity is Severidade.INFO  # uma modalidade só: não alerta de novo
    assert barramento.falhas == []


def test_nao_reprocessa_o_proprio_risco(config, barramento):
    fusao = FusaoMultimodal(config)
    fusao.assinar(barramento)
    barramento.emitir(achado(Modalidade.AUDIO, "fadiga_vocal", minuto=0))
    assert barramento.total_publicados == 2  # achado + risco, sem laço


def test_so_republica_quando_o_risco_muda(config):
    saida = EmissorMemoria()
    fusao = FusaoMultimodal(config, saida)

    fusao.receber(achado(Modalidade.SINAIS_VITAIS, "news2_resposta_urgente", minuto=0))
    fusao.receber(achado(Modalidade.SINAIS_VITAIS, "news2_resposta_urgente", minuto=1))  # igual
    assert len(saida.eventos) == 1

    fusao.receber(achado(Modalidade.AUDIO, "dificuldade_respiratoria", minuto=2))  # sobe o risco
    assert len(saida.eventos) == 2
    assert saida.eventos[-1].score > saida.eventos[0].score


def test_pacientes_sao_independentes(config):
    fusao = FusaoMultimodal(config, EmissorMemoria())
    fusao.receber(achado(Modalidade.SINAIS_VITAIS, "news2_emergencia", "alta", minuto=0))
    fusao.receber(achado(Modalidade.AUDIO, "fadiga_vocal", "baixa", minuto=0, paciente=OUTRO))

    assert fusao.risco(PACIENTE).com_achado == (Modalidade.SINAIS_VITAIS,)
    assert fusao.risco(OUTRO).com_achado == (Modalidade.AUDIO,)
    assert [r.patient_id for r in fusao.pacientes()] == [PACIENTE, OUTRO]  # maior risco primeiro


def test_achado_atrasado_fora_da_janela_e_ignorado(config):
    fusao = FusaoMultimodal(config, EmissorMemoria())
    fusao.receber(achado(Modalidade.AUDIO, "fadiga_vocal", minuto=120))
    assert fusao.receber(achado(Modalidade.SINAIS_VITAIS, "news2_emergencia", "alta", minuto=0)) is None
    assert fusao.risco(PACIENTE).com_achado == (Modalidade.AUDIO,)


def test_achado_atrasado_dentro_da_janela_entra(config):
    fusao = FusaoMultimodal(config, EmissorMemoria())
    fusao.receber(achado(Modalidade.AUDIO, "fadiga_vocal", minuto=30))
    fusao.receber(achado(Modalidade.SINAIS_VITAIS, "news2_emergencia", "alta", minuto=0))
    risco = fusao.risco(PACIENTE)
    assert set(risco.com_achado) == {Modalidade.AUDIO, Modalidade.SINAIS_VITAIS}
    assert risco.ate == instante(30)


def test_buffer_descarta_o_que_saiu_da_janela(config):
    fusao = FusaoMultimodal(config, EmissorMemoria())
    fusao.receber(achado(Modalidade.SINAIS_VITAIS, "news2_emergencia", "alta", minuto=0))
    fusao.receber(achado(Modalidade.AUDIO, "fadiga_vocal", minuto=61))

    assert fusao.risco(PACIENTE).com_achado == (Modalidade.AUDIO,)
    assert len(fusao._buffer[PACIENTE]) == 1
    assert fusao.risco_em(PACIENTE, instante(30)) is None  # o achado antigo já saiu do buffer


def test_falha_em_outro_assinante_nao_para_a_fusao(config, barramento):
    def quebrado(_evento):
        raise RuntimeError("detector com defeito")

    barramento.assinar("quebrado", quebrado)
    fusao = FusaoMultimodal(config)
    fusao.assinar(barramento)
    barramento.emitir(achado(Modalidade.AUDIO, "fadiga_vocal", minuto=0))

    assert fusao.risco(PACIENTE) is not None
    assert barramento.falhas and barramento.falhas[0].assinante == "quebrado"


def test_caso_de_demonstracao_vira_alerta_alta_para_a_equipe(config, barramento):
    """SpO2 em queda + dispneia: cada achado gera alerta médio; a fusão gera o alerta alta."""
    FusaoMultimodal(config).assinar(barramento)
    servico = construir(ConfigAlertas(
        armazenamento="memoria", regras_path=RAIZ / "config" / "alertas_regras.yaml",
    ))
    servico.assinar(barramento)

    barramento.emitir_varios(cenario_dispneia(PACIENTE, T0))
    asyncio.run(servico.processar_pendentes())

    alertas = servico.armazenamento.listar()
    [da_fusao] = [a for a in alertas if a.modalities == ["fusao"]]
    assert da_fusao.priority is Severidade.ALTA
    assert da_fusao.summary.startswith("Risco alto")
    assert da_fusao.evidence["features"]["contrib_sinais_vitais"] > 0
    isolados = [a for a in alertas if a.modalities != ["fusao"]]
    assert isolados and all(a.priority is Severidade.MEDIA for a in isolados)
    assert barramento.falhas == []
