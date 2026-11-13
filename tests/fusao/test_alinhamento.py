"""Alinhamento temporal por patient_id e janela configurável (US18)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from contratos import Modalidade
from fusao import alinhar, alinhar_paciente, na_janela
from fusao.alinhamento import idade, instante_mais_recente

from .conftest import OUTRO, PACIENTE, achado, instante

JANELA = timedelta(minutes=60)


class TestNaJanela:
    @pytest.mark.parametrize(
        ("minuto", "esperado"),
        [
            (-61, False),  # antes do início
            (-60, True),   # exatamente no início: inclusivo
            (-30, True),
            (0, True),     # exatamente em "ate": inclusivo
            (1, False),    # futuro em relação a "ate" não explica o presente
        ],
    )
    def test_achado_pontual(self, minuto, esperado):
        evento = achado(Modalidade.AUDIO, "fadiga_vocal", minuto=minuto)
        assert na_janela(evento, instante(0), JANELA) is esperado

    def test_intervalo_que_comeca_antes_e_termina_dentro_conta(self):
        # imobilidade de 3 h que começou 2 h antes do início da janela e terminou dentro dela
        evento = achado(Modalidade.MOVIMENTACAO, "imobilidade_prolongada", minuto=-180, duracao_min=150)
        assert na_janela(evento, instante(0), JANELA)

    def test_intervalo_que_termina_antes_da_janela_nao_conta(self):
        evento = achado(Modalidade.MOVIMENTACAO, "imobilidade_prolongada", minuto=-180, duracao_min=100)
        assert not na_janela(evento, instante(0), JANELA)

    def test_intervalo_em_curso_tem_idade_zero(self):
        evento = achado(Modalidade.VIDEO, "postura_fora_da_faixa", minuto=-5, duracao_min=10)
        assert idade(evento, instante(0)) == timedelta(0)
        assert idade(achado(Modalidade.AUDIO, "fadiga_vocal", minuto=-20), instante(0)) == timedelta(minutes=20)


class TestAlinharPaciente:
    def test_filtra_paciente_janela_e_fusao(self):
        eventos = [
            achado(Modalidade.SINAIS_VITAIS, "news2_resposta_urgente", minuto=-10),
            achado(Modalidade.SINAIS_VITAIS, "news2_resposta_urgente", minuto=-90),       # fora
            achado(Modalidade.AUDIO, "dificuldade_respiratoria", minuto=-5, paciente=OUTRO),  # outro paciente
        ]
        alinhamento = alinhar_paciente(eventos, PACIENTE, instante(0), JANELA)

        assert [e.event_id for e in alinhamento.eventos] == [eventos[0].event_id]
        assert alinhamento.presentes == (Modalidade.SINAIS_VITAIS,)
        assert alinhamento.inicio == instante(-60) and alinhamento.ate == instante(0)

    def test_evento_repetido_conta_uma_vez(self):
        evento = achado(Modalidade.TEXTO, "termo_critico", minuto=-1)
        alinhamento = alinhar_paciente([evento, evento], PACIENTE, instante(0), JANELA)
        assert len(alinhamento.eventos) == 1

    def test_distingue_ausente_presente_normal_e_com_achado(self):
        eventos = [
            achado(Modalidade.VIDEO, "sem_achados", minuto=-10),
            achado(Modalidade.SINAIS_VITAIS, "news2_emergencia", "alta", minuto=-5),
        ]
        alinhamento = alinhar_paciente(eventos, PACIENTE, instante(0), JANELA)

        assert set(alinhamento.presentes) == {Modalidade.VIDEO, Modalidade.SINAIS_VITAIS}
        assert alinhamento.com_achado == (Modalidade.SINAIS_VITAIS,)
        assert Modalidade.AUDIO in alinhamento.ausentes
        assert Modalidade.VIDEO not in alinhamento.ausentes

    def test_ordem_cronologica_dentro_da_modalidade(self):
        tarde = achado(Modalidade.AUDIO, "fadiga_vocal", minuto=-1)
        cedo = achado(Modalidade.AUDIO, "fadiga_vocal", minuto=-30)
        alinhamento = alinhar_paciente([tarde, cedo], PACIENTE, instante(0), JANELA)
        assert alinhamento.por_modalidade[Modalidade.AUDIO] == (cedo, tarde)

    def test_janela_configuravel_muda_o_que_entra(self):
        eventos = [
            achado(Modalidade.SINAIS_VITAIS, "news2_resposta_urgente", minuto=-45),
            achado(Modalidade.AUDIO, "fadiga_vocal", minuto=0),
        ]
        curta = alinhar_paciente(eventos, PACIENTE, instante(0), timedelta(minutes=30))
        longa = alinhar_paciente(eventos, PACIENTE, instante(0), timedelta(minutes=60))
        assert curta.presentes == (Modalidade.AUDIO,)
        assert set(longa.presentes) == {Modalidade.AUDIO, Modalidade.SINAIS_VITAIS}

    def test_sem_eventos_e_vazio(self):
        assert alinhar_paciente([], PACIENTE, instante(0), JANELA).vazio


class TestAlinharTodos:
    def test_cada_paciente_usa_o_proprio_achado_mais_recente(self):
        a1 = achado(Modalidade.SINAIS_VITAIS, "news2_resposta_urgente", minuto=0)
        a2 = achado(Modalidade.AUDIO, "fadiga_vocal", minuto=50)
        b1 = achado(Modalidade.TEXTO, "termo_critico", minuto=500, paciente=OUTRO)

        resultado = alinhar([a1, a2, b1], JANELA)

        assert set(resultado) == {PACIENTE, OUTRO}
        assert resultado[PACIENTE].ate == instante(50)
        assert len(resultado[PACIENTE].eventos) == 2
        assert resultado[OUTRO].ate == instante(500)

    def test_ate_fixo_descarta_paciente_sem_achados_na_janela(self):
        a1 = achado(Modalidade.SINAIS_VITAIS, "news2_resposta_urgente", minuto=0)
        b1 = achado(Modalidade.TEXTO, "termo_critico", minuto=500, paciente=OUTRO)
        assert set(alinhar([a1, b1], JANELA, ate=instante(10))) == {PACIENTE}

    def test_ignora_eventos_da_propria_fusao(self, config):
        from fusao import calcular_risco, evento_de_risco

        base = achado(Modalidade.AUDIO, "fadiga_vocal", minuto=0)
        risco = calcular_risco(alinhar_paciente([base], PACIENTE, instante(0), JANELA), config)
        fusao = evento_de_risco(risco, config)

        resultado = alinhar([base, fusao], JANELA)
        assert [e.event_id for e in resultado[PACIENTE].eventos] == [base.event_id]

    def test_instante_mais_recente_usa_fim_do_intervalo(self):
        eventos = [
            achado(Modalidade.AUDIO, "fadiga_vocal", minuto=0, duracao_min=20),
            achado(Modalidade.TEXTO, "termo_critico", minuto=10),
        ]
        assert instante_mais_recente(eventos) == instante(20)
        assert instante_mais_recente([]) is None
