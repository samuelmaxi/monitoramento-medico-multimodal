"""Testes da máquina de estados das áreas críticas."""

from __future__ import annotations

from video import Caixa, Transicao

from .conftest import deteccao


def test_fora_para_dentro_emite_entrada(monitor, dentro, fora):
    assert monitor.processar([fora]) == []
    transicoes = monitor.processar([dentro])
    assert len(transicoes) == 1
    assert transicoes[0].transicao is Transicao.ENTRADA


def test_dentro_continuo_nao_repete_evento(monitor, dentro):
    monitor.processar([deteccao(Caixa(300, 300, 380, 380))])
    assert len(monitor.processar([dentro])) == 1
    assert monitor.processar([dentro]) == []
    assert monitor.processar([dentro]) == []


def test_dentro_para_fora_emite_saida(monitor, dentro, fora):
    monitor.processar([fora])
    monitor.processar([dentro])
    transicoes = monitor.processar([fora])
    assert len(transicoes) == 1
    assert transicoes[0].transicao is Transicao.SAIDA


def test_objeto_ausente_tem_tolerancia_de_oclusao(monitor, dentro):
    monitor.primeira_observacao_entra = True
    assert monitor.processar([dentro])[0].transicao is Transicao.ENTRADA
    assert monitor.processar([]) == []
    assert monitor.processar([]) == []
    encerramento = monitor.processar([])
    assert len(encerramento) == 1
    assert encerramento[0].transicao is Transicao.SAIDA


def test_track_ids_separam_objetos_da_mesma_classe(monitor):
    objeto_a_fora = deteccao(Caixa(300, 300, 380, 380), track_id=3)
    objeto_b_fora = deteccao(Caixa(300, 300, 380, 380), track_id=4)
    objeto_a_dentro = deteccao(Caixa(10, 10, 90, 90), track_id=3)
    objeto_b_dentro = deteccao(Caixa(20, 20, 80, 80), track_id=4)
    assert monitor.processar([objeto_a_fora, objeto_b_fora]) == []
    transicoes = monitor.processar([objeto_a_dentro, objeto_b_dentro])
    assert len(transicoes) == 2
    assert {evento.track_id for evento in transicoes} == {3, 4}


def test_quem_ja_estava_dentro_nao_gera_saida_sem_entrada(monitor, dentro, fora):
    assert monitor.processar([dentro]) == []  # primeira observação já dentro: sem entrada
    assert monitor.processar([fora]) == []
    assert monitor.processar([dentro])[0].transicao is Transicao.ENTRADA  # agora pareia
    assert monitor.processar([fora])[0].transicao is Transicao.SAIDA


def test_ausencia_de_quem_entrou_sem_evento_nao_gera_saida(monitor, dentro):
    monitor.processar([dentro])
    assert monitor.processar([]) == []
    assert monitor.processar([]) == []
    assert monitor.processar([]) == []  # passou da persistência: fecha calado


def test_objeto_fora_que_some_e_volta_dentro_emite_entrada(monitor, dentro, fora):
    monitor.processar([fora])
    for _ in range(monitor.persistencia_quadros + 3):
        assert monitor.processar([]) == []
    assert monitor.processar([dentro])[0].transicao is Transicao.ENTRADA
    assert monitor.processar([fora])[0].transicao is Transicao.SAIDA


def test_saida_por_ausencia_mantem_estado_para_nova_entrada(monitor, dentro, fora):
    monitor.processar([fora])
    monitor.processar([dentro])
    for _ in range(monitor.persistencia_quadros):
        assert monitor.processar([]) == []
    assert monitor.processar([])[0].transicao is Transicao.SAIDA
    assert monitor.processar([]) == []  # não repete a saída
    assert monitor.processar([dentro])[0].transicao is Transicao.ENTRADA


def test_primeira_observacao_dentro_respeita_configuracao(monitor, dentro):
    assert monitor.processar([dentro]) == []
    monitor.reiniciar()
    monitor.primeira_observacao_entra = True
    transicoes = monitor.processar([dentro])
    assert len(transicoes) == 1
    assert transicoes[0].transicao is Transicao.ENTRADA
