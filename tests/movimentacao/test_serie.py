from movimentacao.modelos import Posicao as P
from movimentacao.serie import marcos, metricas, periodo, trechos_sem_mudanca

from .conftest import instante, obs


def _tipos(ms):
    return [m.tipo for m in ms]


def test_noite_cruza_meia_noite(cfg):
    assert periodo(instante("23:30"), cfg) == "noite"
    assert periodo(instante("05:59"), cfg) == "noite"
    assert periodo(instante("22:00"), cfg) == "noite"
    assert periodo(instante("06:00"), cfg) == "dia"
    assert periodo(instante("21:59"), cfg) == "dia"


def test_mudanca_de_posicao(cfg):
    ms = marcos([obs("08:00", P.SUPINO), obs("10:00", P.LATERAL_ESQ), obs("11:00", P.LATERAL_ESQ)], cfg)
    assert _tipos(ms) == ["mudanca_posicao"]
    assert ms[0].instante == instante("10:00")
    assert (ms[0].de, ms[0].para) == (P.SUPINO, P.LATERAL_ESQ)


def test_ordem_de_chegada_nao_importa(cfg):
    ms = marcos([obs("10:00", P.LATERAL_ESQ), obs("08:00", P.SUPINO)], cfg)
    assert _tipos(ms) == ["mudanca_posicao"]


def test_turn_sem_posicao_conta(cfg):
    ms = marcos([obs("08:00", P.SUPINO), obs("10:00", P.DESCONHECIDA, mudanca=True)], cfg)
    assert _tipos(ms) == ["mudanca_posicao"]
    assert ms[0].de == ms[0].para == P.SUPINO


def test_saida_do_leito_noturna(cfg):
    ms = marcos([obs("22:30", P.SUPINO), obs("23:10", P.FORA_DO_LEITO), obs("23:30", P.SUPINO)], cfg)
    assert _tipos(ms) == ["saida_leito", "retorno_leito"]
    assert ms[0].periodo == "noite"


def test_fora_do_leito_repetido_e_uma_saida(cfg):
    ms = marcos([obs("10:00", P.SUPINO), obs("10:10", P.FORA_DO_LEITO), obs("10:20", P.FORA_DO_LEITO)], cfg)
    assert _tipos(ms) == ["saida_leito"]


def test_queda(cfg):
    ms = marcos([obs("02:00", P.SUPINO), obs("03:00", P.CHAO)], cfg)
    assert _tipos(ms) == ["queda"]
    assert ms[0].periodo == "noite"


def test_queda_sinalizada_pelo_detector(cfg):
    ms = marcos([obs("02:00", P.SUPINO), obs("03:00", P.DESCONHECIDA, queda=True)], cfg)
    assert _tipos(ms) == ["queda"]


def test_rotativo_e_desconhecida_nao_mudam(cfg):
    serie = [obs("08:00", P.SUPINO), obs("09:00", P.ROTATIVO), obs("10:00", P.DESCONHECIDA), obs("11:00", P.SUPINO)]
    assert marcos(serie, cfg) == []


def test_desconhecida_nao_apaga_a_posicao_anterior(cfg):
    ms = marcos([obs("08:00", P.SUPINO), obs("09:00", P.DESCONHECIDA), obs("10:00", P.LATERAL_DIR)], cfg)
    assert _tipos(ms) == ["mudanca_posicao"]
    assert ms[0].de == P.SUPINO


def test_trecho_sem_mudanca(cfg):
    serie = [obs("08:00", P.SUPINO), obs("09:00", P.SUPINO), obs("10:30", P.SUPINO), obs("11:00", P.LATERAL_ESQ)]
    trechos = trechos_sem_mudanca(serie, cfg)
    assert trechos[0].inicio == instante("08:00")
    assert trechos[0].fim == instante("11:00")
    assert trechos[0].minutos == 180
    assert trechos[0].confiavel


def test_lacuna_quebra_trecho(cfg):
    trechos = trechos_sem_mudanca([obs("08:00", P.SUPINO), obs("12:00", P.SUPINO)], cfg)
    assert len(trechos) == 1
    assert not trechos[0].confiavel


def test_rotativo_e_fora_do_leito_nao_entram_no_trecho(cfg):
    serie = [obs("08:00", P.ROTATIVO), obs("10:00", P.ROTATIVO), obs("11:00", P.FORA_DO_LEITO), obs("12:00", P.FORA_DO_LEITO)]
    assert trechos_sem_mudanca(serie, cfg) == []


def test_saida_encerra_trecho_e_retorno_abre_outro(cfg):
    serie = [obs("08:00", P.SUPINO), obs("09:00", P.FORA_DO_LEITO), obs("09:30", P.SUPINO), obs("10:00", P.SUPINO)]
    trechos = trechos_sem_mudanca(serie, cfg)
    assert [(t.inicio, t.fim) for t in trechos] == [
        (instante("08:00"), instante("09:00")),
        (instante("09:30"), instante("10:00")),
    ]


def test_trecho_aberto_vai_ate_o_instante_pedido(cfg):
    trechos = trechos_sem_mudanca([obs("08:00", P.SUPINO), obs("09:00", P.SUPINO)], cfg, ate=instante("10:00"))
    assert trechos[-1].fim == instante("10:00")
    assert trechos[-1].aberto


def _seis_horas():
    return [
        obs("08:00", P.SUPINO),
        obs("09:00", P.LATERAL_ESQ),
        obs("10:00", P.LATERAL_ESQ),
        obs("11:00", P.LATERAL_DIR),
        obs("12:00", P.FORA_DO_LEITO),
        obs("12:30", P.SUPINO),
        obs("13:00", P.SUPINO),
        obs("14:00", P.LATERAL_ESQ),
    ]


def test_metricas(cfg):
    m = metricas(_seis_horas(), cfg)
    assert m["horas_observadas"] == 6
    assert m["reposicionamentos_por_hora"] == 0.5
    assert m["saidas_do_leito"] == 1
    assert m["saidas_do_leito_noite"] == 0
    assert m["quedas"] == 0
    assert m["maior_minutos_sem_mudanca"] == 120
    assert m["minutos_sem_mudanca"] == 0


def test_metricas_tempo_sem_mudanca_atual(cfg):
    m = metricas(_seis_horas(), cfg, ate=instante("15:00"))
    assert m["minutos_sem_mudanca"] == 60
    assert m["horas_observadas"] == 7


def test_metricas_serie_vazia(cfg):
    m = metricas([], cfg)
    assert m["horas_observadas"] == 0
    assert m["reposicionamentos_por_hora"] == 0


def test_mesma_troca_registrada_por_duas_fontes_conta_uma_vez(cfg):
    # MIMIC: Position e Turn no mesmo charttime descrevem a mesma troca.
    serie = [
        obs("08:00", P.SUPINO, fonte="mimic_position"),
        obs("10:00", P.LATERAL_ESQ, fonte="mimic_position"),
        obs("10:00", P.DESCONHECIDA, fonte="mimic_turn", mudanca=True),
    ]
    assert _tipos(marcos(serie, cfg)) == ["mudanca_posicao"]
    assert metricas(serie, cfg)["mudancas_de_posicao"] == 1
