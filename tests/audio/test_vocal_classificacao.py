"""US14: regras de classificação (fadiga, dificuldade respiratória, possível disartria, normal)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from audio.vocal.baseline import ComparacaoBaseline
from audio.vocal.biomarcadores import BiomarcadoresAudio
from audio.vocal.classificacao import (
    AVISO,
    CLASSE_NORMAL,
    classificar,
    limiares_da_classe,
)
from audio.vocal.respiratorio import EventoRespiratorio, ResumoRespiratorio
from contratos import Severidade

NORMAL = BiomarcadoresAudio(
    segmentos=4, inicio_s=0.3, fim_s=11.0, palavras=32, duracao_fala_s=11.1, vozeado_s=6.0,
    pausa_total_s=1.5, n_pausas=3, pausa_media_s=0.5, proporcao_pausas=0.135,
    taxa_fala_pps=2.9, taxa_articulacao_pps=3.3, f0_media_hz=145.0, f0_desvio_st=3.4,
    jitter_local_pct=0.3, shimmer_local_pct=1.2, hnr_db=26.0, intensidade_db=80.0,
)


def bio(**mudancas) -> BiomarcadoresAudio:
    return replace(NORMAL, **mudancas)


def respiratorio(*scores: float, tipo="tosse") -> ResumoRespiratorio:
    eventos = tuple(
        EventoRespiratorio(tipo, "Cough", 1.0 + i, 2.0 + i, s, s, 2, Severidade.BAIXA)
        for i, s in enumerate(scores))
    return ResumoRespiratorio(disponivel=True, eventos=eventos)


def classes(resultado) -> set[str]:
    return {c.event_type for c in resultado.classes}


def baseline_com(**medianas) -> ComparacaoBaseline:
    return ComparacaoBaseline(audios=5, suficiente=True, medianas=medianas, desvios=())


# --- normal --------------------------------------------------------------------------------- #


def test_voz_normal_nao_dispara_classe_nenhuma_e_e_a_classe_normal(cfg_vocal):
    resultado = classificar(NORMAL, respiratorio(), None, cfg_vocal)

    assert resultado.classes == ()
    assert resultado.classe_principal == CLASSE_NORMAL


# --- dificuldade respiratória --------------------------------------------------------------- #


def test_dois_criterios_dao_dificuldade_respiratoria_de_severidade_media(cfg_vocal):
    resultado = classificar(
        bio(proporcao_pausas=0.5), respiratorio(0.5, 0.5), None, cfg_vocal)

    (classe,) = resultado.classes
    assert classe.event_type == "dificuldade_respiratoria"
    assert {a.nome for a in classe.satisfeitos} == {"eventos_respiratorios", "pausas_altas"}
    assert classe.severidade is Severidade.MEDIA


def test_tres_criterios_elevam_a_dificuldade_respiratoria_para_alta(cfg_vocal):
    """Severidade alta é a que o motor de alertas (US19) envia."""
    resultado = classificar(
        bio(proporcao_pausas=0.5, taxa_fala_pps=1.2), respiratorio(0.5, 0.5), None, cfg_vocal)

    (classe,) = [c for c in resultado.classes if c.event_type == "dificuldade_respiratoria"]
    assert classe.severidade is Severidade.ALTA
    assert len(classe.satisfeitos) == 3
    assert classe.score == pytest.approx(3 / 4)


def test_um_unico_evento_respiratorio_forte_basta_para_o_criterio(cfg_vocal):
    forte = cfg_vocal.limiares.score_respiratorio_forte

    resultado = classificar(bio(proporcao_pausas=0.5), respiratorio(forte), None, cfg_vocal)

    assert "dificuldade_respiratoria" in classes(resultado)


def test_um_unico_evento_fraco_nao_basta(cfg_vocal):
    resultado = classificar(bio(proporcao_pausas=0.5), respiratorio(0.35), None, cfg_vocal)

    assert "dificuldade_respiratoria" not in classes(resultado)


def test_sem_o_detector_respiratorio_o_criterio_nao_conta_e_e_declarado(cfg_vocal):
    resultado = classificar(
        bio(proporcao_pausas=0.5, taxa_fala_pps=1.2), ResumoRespiratorio(disponivel=False), None,
        cfg_vocal)

    (classe,) = [c for c in resultado.classes if c.event_type == "dificuldade_respiratoria"]
    assert "eventos_respiratorios" in classe.nao_avaliados
    assert classe.severidade is Severidade.MEDIA  # 2 critérios, e o respiratório não foi contado
    assert not resultado.avaliacoes["eventos_respiratorios"].avaliavel


def test_voz_alterada_com_fala_lenta_sem_sinal_respiratorio_nem_pausas_nao_e_dificuldade(cfg_vocal):
    """É o padrão da disartria: HNR baixo e fala lenta, sem evento respiratório nem pausas."""
    resultado = classificar(
        bio(hnr_db=12.0, taxa_fala_pps=1.0, taxa_articulacao_pps=1.0, proporcao_pausas=0.1),
        respiratorio(), None, cfg_vocal)

    assert "dificuldade_respiratoria" not in classes(resultado)
    assert resultado.avaliacoes["hnr_baixo"].satisfeito
    assert resultado.avaliacoes["fala_lenta"].satisfeito  # 2 critérios, mas nenhum respiratório


def test_pausas_frequentes_bastam_como_sinal_respiratorio_quando_nao_ha_yamnet(cfg_vocal):
    resultado = classificar(
        bio(hnr_db=12.0, proporcao_pausas=0.5), ResumoRespiratorio(disponivel=False), None,
        cfg_vocal)

    assert "dificuldade_respiratoria" in classes(resultado)


# --- fadiga --------------------------------------------------------------------------------- #


def test_fala_monotona_com_pausas_altas_da_fadiga_vocal(cfg_vocal):
    resultado = classificar(
        bio(f0_desvio_st=1.0, proporcao_pausas=0.5), respiratorio(), None, cfg_vocal)

    fadiga = [c for c in resultado.classes if c.event_type == "fadiga_vocal"]
    assert len(fadiga) == 1
    assert fadiga[0].severidade is Severidade.MEDIA
    assert {a.nome for a in fadiga[0].satisfeitos} == {"f0_monotona", "pausas_altas"}


def test_queda_de_intensidade_so_vale_com_baseline_do_paciente(cfg_vocal):
    com = classificar(bio(intensidade_db=70.0, f0_desvio_st=1.0), None,
                      baseline_com(intensidade_db=80.0), cfg_vocal)
    sem = classificar(bio(intensidade_db=70.0, f0_desvio_st=1.0), None, None, cfg_vocal)

    assert "fadiga_vocal" in classes(com)
    assert "fadiga_vocal" not in classes(sem)  # só f0_monotona: 1 critério, e sem baseline
    assert not sem.avaliacoes["intensidade_caiu"].avaliavel


def test_queda_de_intensidade_abaixo_do_minimo_nao_conta(cfg_vocal):
    queda = cfg_vocal.limiares.intensidade_queda_baseline_db

    resultado = classificar(
        bio(intensidade_db=80.0 - queda + 1, f0_desvio_st=1.0), None,
        baseline_com(intensidade_db=80.0), cfg_vocal)

    assert not resultado.avaliacoes["intensidade_caiu"].satisfeito


# --- possível disartria --------------------------------------------------------------------- #


ALTERADA = {"jitter_local_pct": 2.0, "shimmer_local_pct": 6.0, "hnr_db": 12.0}


def test_qualidade_vocal_alterada_sozinha_nao_e_disartria(cfg_vocal):
    """Jitter, shimmer e HNR alterados também ocorrem em disfonia: exige-se fala lenta/pausada."""
    resultado = classificar(bio(**ALTERADA), respiratorio(), None, cfg_vocal)

    assert "possivel_disartria" not in classes(resultado)
    assert all(resultado.avaliacoes[n].satisfeito for n in ("jitter_alto", "shimmer_alto", "hnr_baixo"))


def test_qualidade_vocal_alterada_com_articulacao_lenta_e_possivel_disartria(cfg_vocal):
    resultado = classificar(
        bio(**ALTERADA, taxa_articulacao_pps=1.0), respiratorio(), None, cfg_vocal)

    (classe,) = [c for c in resultado.classes if c.event_type == "possivel_disartria"]
    assert classe.severidade is Severidade.MEDIA
    assert "articulacao_lenta" in {a.nome for a in classe.satisfeitos}
    assert len(classe.satisfeitos) == 4


def test_disartria_sem_transcricao_nao_dispara_pela_articulacao_mas_pode_pelas_pausas(cfg_vocal):
    sem_palavras = {"taxa_fala_pps": None, "taxa_articulacao_pps": None, "palavras": None}

    sem_pausas = classificar(bio(**ALTERADA, **sem_palavras), respiratorio(), None, cfg_vocal)
    com_pausas = classificar(
        bio(**ALTERADA, **sem_palavras, proporcao_pausas=0.5), respiratorio(), None, cfg_vocal)

    assert "possivel_disartria" not in classes(sem_pausas)
    assert "possivel_disartria" in classes(com_pausas)
    assert not sem_pausas.avaliacoes["articulacao_lenta"].avaliavel


# --- limites e dados ausentes --------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("criterio", "campo", "no_limite"),
    [
        ("jitter_alto", "jitter_local_pct", 1.04),
        ("shimmer_alto", "shimmer_local_pct", 3.81),
        ("hnr_baixo", "hnr_db", 20.0),
    ],
)
def test_valor_exatamente_no_limiar_da_us12_nao_e_alteracao(cfg_vocal, criterio, campo, no_limite):
    """A US12 diz jitter > 1,04, shimmer > 3,81 e HNR < 20: no limiar, ainda é normal."""
    resultado = classificar(bio(**{campo: no_limite}), None, None, cfg_vocal)

    assert not resultado.avaliacoes[criterio].satisfeito
    assert resultado.avaliacoes[criterio].limiar == no_limite


def test_criterio_sem_medida_nao_e_avaliavel_e_nao_conta(cfg_vocal):
    resultado = classificar(
        bio(jitter_local_pct=None, shimmer_local_pct=None, hnr_db=None, f0_desvio_st=None),
        None, None, cfg_vocal)

    for nome in ("jitter_alto", "shimmer_alto", "hnr_baixo", "f0_monotona"):
        avaliacao = resultado.avaliacoes[nome]
        assert not avaliacao.avaliavel
        assert not avaliacao.satisfeito
        assert "não avaliável" in avaliacao.texto
    assert resultado.classes == ()


def test_varias_classes_podem_disparar_juntas_e_a_principal_e_a_mais_grave(cfg_vocal):
    resultado = classificar(
        bio(proporcao_pausas=0.6, taxa_fala_pps=1.0, f0_desvio_st=1.0, **ALTERADA),
        respiratorio(0.5, 0.5), None, cfg_vocal)

    assert classes(resultado) >= {"dificuldade_respiratoria", "fadiga_vocal"}
    assert resultado.classe_principal == "dificuldade_respiratoria"  # severidade alta


# --- explicabilidade ------------------------------------------------------------------------ #


def test_cada_classe_lista_os_criterios_com_valor_limiar_e_frase(cfg_vocal):
    resultado = classificar(
        bio(proporcao_pausas=0.5), respiratorio(0.5, 0.5), None, cfg_vocal)

    (classe,) = resultado.classes
    pausas = next(a for a in classe.satisfeitos if a.nome == "pausas_altas")
    assert pausas.valor == pytest.approx(0.5)
    assert pausas.limiar == cfg_vocal.limiares.proporcao_pausas_max
    assert pausas.texto == "pausas ocupam 50% da fala (limite 40%)"
    assert classe.regras_acionadas == "eventos_respiratorios, pausas_altas"
    dados = classe.para_dict()
    assert dados["criterios_atendidos"] == 2
    assert dados["criterios_total"] == 4


def test_limiares_da_classe_trazem_so_os_que_ela_usa(cfg_vocal):
    disartria = limiares_da_classe(cfg_vocal.regras["possivel_disartria"], cfg_vocal.limiares)
    fadiga = limiares_da_classe(cfg_vocal.regras["fadiga_vocal"], cfg_vocal.limiares)

    assert disartria["jitter_local_pct_max"] == 1.04
    assert disartria["shimmer_local_pct_max"] == 3.81
    assert disartria["hnr_db_min"] == 20.0
    assert "score_respiratorio_forte" not in disartria
    assert "jitter_local_pct_max" not in fadiga
    assert "intensidade_queda_baseline_db" in fadiga


def test_aviso_deixa_claro_que_nao_e_diagnostico():
    assert "avaliação clínica" in AVISO
    assert "não é diagnóstico" in AVISO
