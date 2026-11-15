"""Cálculo do score de risco: força, combinação, contribuições, concordância e níveis (US18)."""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import timedelta

import pytest

from contratos import Modalidade, Severidade
from fusao import alinhar_paciente, calcular_risco
from fusao.cenarios import cenario_dispneia, cenario_sem_video
from fusao.score import atribuir, forca_do_achado, nivel_do_score, noisy_or

from .conftest import PACIENTE, T0, achado, instante


def risco_de(eventos, config, ate=None):
    ate = ate or max(e.window.end if e.window else e.timestamp for e in eventos)
    return calcular_risco(alinhar_paciente(eventos, PACIENTE, ate, config.janela), config)


# Um achado positivo por modalidade, sem eixo clínico em comum (concordância fica de fora).
POSITIVOS = {
    Modalidade.SINAIS_VITAIS: ("news2_resposta_urgente", "media"),
    Modalidade.AUDIO: ("possivel_disartria", "media"),
    Modalidade.TEXTO: ("sentimento_negativo", "media"),
    Modalidade.VIDEO: ("postura_fora_da_faixa", "media"),
    Modalidade.PRESCRICOES: ("mudanca_abrupta_dose", "media"),
    Modalidade.MOVIMENTACAO: ("imobilidade_prolongada", "media"),
}


def positivo(modalidade, minuto=0, **extra):
    tipo, severidade = POSITIVOS[modalidade]
    return achado(modalidade, tipo, severidade, minuto=minuto, **extra)


class TestFuncoesBase:
    def test_noisy_or(self):
        assert noisy_or([]) == 0
        assert noisy_or([0.5]) == pytest.approx(0.5)
        assert noisy_or([0.5, 0.5]) == pytest.approx(0.75)
        assert noisy_or([0.0, 0.0]) == 0

    def test_atribuir_soma_o_total_e_nulo_fica_zero(self):
        partes = atribuir([0.5, 0.0, 0.2], 0.6)
        assert sum(partes) == pytest.approx(0.6)
        assert partes[1] == 0.0
        assert partes[0] > partes[2] > 0
        assert atribuir([0.0, 0.0], 0.0) == [0.0, 0.0]

    @pytest.mark.parametrize(("score", "nivel"), [(0.0, "baixo"), (0.39, "baixo"), (0.4, "medio"),
                                                   (0.69, "medio"), (0.7, "alto"), (1.0, "alto")])
    def test_niveis(self, config, score, nivel):
        assert nivel_do_score(score, config) == nivel

    def test_forca_segue_a_formula_documentada(self, config):
        evento = achado(Modalidade.AUDIO, "fadiga_vocal", "media", score=0.8)
        esperado = 0.65 * 0.6 * (1 - 0.3 + 0.3 * 0.8)  # peso × severidade × modulação
        assert forca_do_achado(evento, instante(0), config) == pytest.approx(esperado)

    def test_severidade_e_a_base_nao_o_score(self, config):
        # NEWS2 = 8 sai com score 0,4 (total/20) e severidade alta; texto com confiança 0,99 e média.
        news2 = achado(Modalidade.SINAIS_VITAIS, "news2_emergencia", "alta", score=0.4)
        texto = achado(Modalidade.TEXTO, "sentimento_negativo", "media", score=0.99)
        assert forca_do_achado(news2, instante(0), config) > forca_do_achado(texto, instante(0), config)

    def test_peso_por_tipo_sobrescreve_o_da_modalidade(self, config):
        queda = achado(Modalidade.VIDEO, "mudanca_brusca_ou_queda", "alta", score=1.0)
        postura = achado(Modalidade.VIDEO, "postura_fora_da_faixa", "alta", score=1.0)
        assert forca_do_achado(queda, instante(0), config) == pytest.approx(0.85 * 0.9)
        assert forca_do_achado(postura, instante(0), config) == pytest.approx(0.6 * 0.9)

    def test_decaimento_por_meia_vida(self, config):
        recente = achado(Modalidade.AUDIO, "fadiga_vocal", minuto=0)
        antigo = achado(Modalidade.AUDIO, "fadiga_vocal", minuto=-60)  # uma meia-vida
        f_recente = forca_do_achado(recente, instante(0), config)
        assert forca_do_achado(antigo, instante(0), config) == pytest.approx(f_recente / 2)

        sem_decaimento = replace(config, meia_vida=None)
        assert forca_do_achado(antigo, instante(0), sem_decaimento) == pytest.approx(f_recente)


class TestScore:
    def test_uma_modalidade_score_igual_a_forca_isolada(self, config):
        evento = positivo(Modalidade.SINAIS_VITAIS)
        risco = risco_de([evento], config)
        assert risco.score == pytest.approx(forca_do_achado(evento, instante(0), config))
        assert risco.contribuicoes["sinais_vitais"] == pytest.approx(risco.score)

    @pytest.mark.parametrize("tamanho", [1, 2, 3, 6])
    def test_contribuicoes_somam_o_score_e_ausentes_sao_zero(self, config, tamanho):
        for combinacao in itertools.combinations(POSITIVOS, tamanho):
            risco = risco_de([positivo(m) for m in combinacao], config)
            assert sum(risco.contribuicoes.values()) == pytest.approx(risco.score)
            for m in Modalidade:
                if m is Modalidade.FUSAO:
                    continue
                if m in combinacao:
                    assert risco.contribuicoes[m.value] > 0
                else:
                    assert risco.contribuicoes[m.value] == 0

    def test_combinacao_sempre_supera_o_maior_sinal_isolado(self, config):
        for a, b in itertools.combinations(POSITIVOS, 2):
            risco = risco_de([positivo(a), positivo(b)], config)
            assert risco.score > risco.score_max_isolado
            assert risco.ganho_fusao > 0

    def test_score_fica_entre_0_e_1_com_tudo_no_maximo(self, config):
        eventos = [
            achado(m, tipo, "alta", score=1.0) for m, (tipo, _) in POSITIVOS.items()
        ]
        risco = risco_de(eventos, config)
        assert 0 < risco.score < 1
        assert risco.nivel == "alto"

    def test_varios_achados_da_mesma_modalidade_nao_somam(self, config):
        um = risco_de([positivo(Modalidade.VIDEO)], config)
        dez = risco_de([positivo(Modalidade.VIDEO) for _ in range(10)], config)
        assert dez.score == pytest.approx(um.score)

    def test_usa_o_achado_mais_forte_da_modalidade(self, config):
        fraco = achado(Modalidade.SINAIS_VITAIS, "desvio_baseline", "baixa", minuto=-1)
        forte = achado(Modalidade.SINAIS_VITAIS, "news2_emergencia", "alta", minuto=-2)
        risco = risco_de([fraco, forte], config, ate=instante(0))
        assert risco.sinais[Modalidade.SINAIS_VITAIS].evento.event_id == forte.event_id


class TestModalidadeAusente:
    def test_paciente_sem_video_nao_falha(self, config):
        risco = risco_de(cenario_sem_video(PACIENTE, T0), config)
        assert Modalidade.VIDEO in risco.ausentes
        assert risco.sinais[Modalidade.VIDEO].forca == 0
        assert risco.contribuicoes["video"] == 0
        assert risco.nivel == "alto"

    def test_so_sem_achados_da_risco_zero(self, config):
        eventos = [achado(m, "sem_achados") for m in POSITIVOS]
        risco = risco_de(eventos, config)
        assert risco.score == 0
        assert risco.nivel == "baixo"
        assert risco.severidade is Severidade.INFO
        assert len(risco.presentes) == 6 and risco.com_achado == ()

    def test_ausente_e_presente_normal_dao_o_mesmo_score(self, config):
        base = [positivo(Modalidade.SINAIS_VITAIS)]
        com_video_normal = [*base, achado(Modalidade.VIDEO, "sem_achados")]
        r_ausente, r_normal = risco_de(base, config), risco_de(com_video_normal, config)
        assert r_ausente.score == pytest.approx(r_normal.score)
        assert Modalidade.VIDEO in r_ausente.ausentes
        assert Modalidade.VIDEO in r_normal.presentes


class TestConcordancia:
    def test_eixo_respiratorio_entre_sinais_e_audio(self, config):
        vitais = achado(Modalidade.SINAIS_VITAIS, "news2_resposta_urgente", "media", score=0.25)
        audio = achado(Modalidade.AUDIO, "dificuldade_respiratoria", "media", score=0.6)
        risco = risco_de([vitais, audio], config)

        assert risco.concordancia is not None
        assert risco.concordancia.eixo == "respiratorio"
        assert set(risco.concordancia.modalidades) == {Modalidade.SINAIS_VITAIS, Modalidade.AUDIO}
        segunda = min(risco.sinais[Modalidade.SINAIS_VITAIS].forca, risco.sinais[Modalidade.AUDIO].forca)
        assert risco.concordancia.termo == pytest.approx(config.fator_concordancia * segunda)
        assert risco.contribuicoes["concordancia"] > 0

    def test_concordancia_eleva_acima_da_combinacao_sem_eixo(self, config):
        eventos = [
            achado(Modalidade.SINAIS_VITAIS, "news2_resposta_urgente", "media", score=0.25),
            achado(Modalidade.AUDIO, "dificuldade_respiratoria", "media", score=0.6),
        ]
        com = risco_de(eventos, config)
        sem = risco_de(eventos, replace(config, fator_concordancia=0))
        assert sem.concordancia is None
        assert com.score > sem.score

    @pytest.mark.parametrize(("termo", "concorda"), [("falta de ar", True), ("Falta de Ar", True),
                                                      ("febre", False), (None, False)])
    def test_texto_so_concorda_com_o_termo_do_eixo(self, config, termo, concorda):
        features = {"termo": termo} if termo else {}
        eventos = [
            achado(Modalidade.SINAIS_VITAIS, "news2_resposta_urgente", "media"),
            achado(Modalidade.TEXTO, "termo_critico", "media", features=features),
        ]
        assert (risco_de(eventos, config).concordancia is not None) is concorda

    def test_modalidades_sem_eixo_comum_nao_concordam(self, config):
        risco = risco_de([positivo(Modalidade.VIDEO), positivo(Modalidade.PRESCRICOES)], config)
        assert risco.concordancia is None


class TestSeveridadeDoRisco:
    def test_uma_modalidade_nao_alerta_de_novo(self, config):
        risco = risco_de([achado(Modalidade.SINAIS_VITAIS, "news2_emergencia", "alta", score=0.4)], config)
        assert risco.nivel == "alto"
        assert risco.severidade is Severidade.INFO

    def test_uma_modalidade_alerta_se_configurado(self, config):
        cfg = replace(config, alertar_com_uma_modalidade=True)
        risco = risco_de([achado(Modalidade.SINAIS_VITAIS, "news2_emergencia", "alta", score=0.4)], cfg)
        assert risco.severidade is Severidade.ALTA

    def test_duas_modalidades_seguem_o_nivel(self, config):
        risco = risco_de([positivo(Modalidade.VIDEO), positivo(Modalidade.PRESCRICOES)], config)
        assert risco.nivel == "medio" and risco.severidade is Severidade.MEDIA


class TestCasoDeDemonstracao:
    """SpO2 em queda + fala com dispneia + relato de falta de ar (DoD da US18)."""

    def test_combinacao_eleva_alem_de_cada_sinal_isolado(self, config):
        eventos = cenario_dispneia(PACIENTE, T0)
        ate = max(e.window.end if e.window else e.timestamp for e in eventos)
        combinado = risco_de(eventos, config, ate=ate)

        isolados = {}
        for m in combinado.com_achado:
            isolados[m] = risco_de([e for e in eventos if e.modality is m], config, ate=ate)

        assert set(isolados) == {Modalidade.SINAIS_VITAIS, Modalidade.AUDIO, Modalidade.TEXTO}
        assert all(r.nivel != "alto" for r in isolados.values())
        assert combinado.nivel == "alto"
        assert combinado.severidade is Severidade.ALTA
        assert combinado.score > max(r.score for r in isolados.values())
        assert combinado.concordancia.eixo == "respiratorio"
        assert {Modalidade.VIDEO, Modalidade.MOVIMENTACAO} <= set(combinado.ausentes)

    def test_janela_curta_deixa_o_sinal_vital_de_fora(self, config):
        eventos = cenario_dispneia(PACIENTE, T0)
        ate = max(e.window.end if e.window else e.timestamp for e in eventos)
        curta = risco_de(eventos, config.com_janela(5), ate=ate)
        assert Modalidade.SINAIS_VITAIS not in curta.com_achado
        assert curta.score < risco_de(eventos, config, ate=ate).score


def test_meia_vida_curta_reduz_achado_antigo(config):
    cfg = replace(config, meia_vida=timedelta(minutes=10))
    antigo = [positivo(Modalidade.SINAIS_VITAIS, minuto=-30), positivo(Modalidade.AUDIO, minuto=0)]
    assert risco_de(antigo, cfg).sinais[Modalidade.SINAIS_VITAIS].forca == pytest.approx(
        risco_de(antigo, replace(config, meia_vida=None)).sinais[Modalidade.SINAIS_VITAIS].forca / 8
    )
