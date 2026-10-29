import dataclasses
from datetime import timedelta

from contratos import Severidade
from movimentacao.modelos import Observacao
from movimentacao.modelos import Posicao as P
from movimentacao.regras import detectar

from .conftest import instante, obs


def _cada_30min(posicao, de: str, ate: str) -> list[Observacao]:
    t, fim = instante(de), instante(ate)
    serie = []
    while t <= fim:
        serie.append(Observacao(t, posicao, "teste"))
        t += timedelta(minutes=30)
    return serie


def _tipo(anomalias, event_type):
    return [a for a in anomalias if a.event_type == event_type]


def _com_limiar(cfg, minutos):
    return dataclasses.replace(cfg, imobilidade=dataclasses.replace(cfg.imobilidade, limiar_min=minutos))


# --- imobilidade --------------------------------------------------------------


def test_imobilidade_acima_de_2h(cfg):
    serie = [*_cada_30min(P.SUPINO, "08:00", "10:30"), obs("10:40", P.LATERAL_ESQ)]
    [a] = _tipo(detectar(serie, cfg), "imobilidade_prolongada")
    assert a.features["minutos_sem_mudanca"] == 160
    assert a.severidade is Severidade.MEDIA
    assert (a.inicio, a.fim) == (instante("08:00"), instante("10:40"))
    assert a.thresholds["limiar_min"] == 120
    assert 0.5 <= a.score < 1


def test_imobilidade_exatamente_2h_nao_dispara(cfg):
    serie = [*_cada_30min(P.SUPINO, "08:00", "09:30"), obs("10:00", P.LATERAL_ESQ)]
    assert _tipo(detectar(serie, cfg), "imobilidade_prolongada") == []


def test_imobilidade_4h_e_alta(cfg):
    serie = [*_cada_30min(P.SUPINO, "08:00", "12:00"), obs("12:10", P.LATERAL_ESQ)]
    [a] = _tipo(detectar(serie, cfg), "imobilidade_prolongada")
    assert a.severidade is Severidade.ALTA
    assert a.score == 1.0


def test_imobilidade_em_curso_ate_agora(cfg):
    serie = _cada_30min(P.SUPINO, "08:00", "10:30")
    [a] = _tipo(detectar(serie, cfg), "imobilidade_prolongada")
    assert a.features["minutos_sem_mudanca"] == 150


def test_limiar_configuravel_90(cfg):
    serie = [*_cada_30min(P.SUPINO, "08:00", "09:30"), obs("09:40", P.LATERAL_ESQ)]
    assert _tipo(detectar(serie, cfg), "imobilidade_prolongada") == []
    assert len(_tipo(detectar(serie, _com_limiar(cfg, 90)), "imobilidade_prolongada")) == 1


def test_lacuna_nao_vira_imobilidade(cfg):
    serie = [obs("08:00", P.SUPINO), obs("12:00", P.SUPINO), obs("12:10", P.LATERAL_DIR)]
    assert _tipo(detectar(serie, cfg), "imobilidade_prolongada") == []


def test_cama_rotativa_nao_vira_imobilidade(cfg):
    serie = _cada_30min(P.ROTATIVO, "08:00", "14:00")
    assert detectar(serie, cfg) == []


# --- saída do leito e queda ----------------------------------------------------


def test_saida_noturna_alta(cfg):
    serie = [obs("22:30", P.SUPINO), obs("23:10", P.FORA_DO_LEITO), obs("23:30", P.SUPINO)]
    [a] = _tipo(detectar(serie, cfg), "saida_do_leito")
    assert a.severidade is Severidade.ALTA
    assert a.features["periodo"] == "noite"
    assert a.inicio == instante("23:10")


def test_saida_diurna_media(cfg):
    serie = [obs("14:00", P.SUPINO), obs("14:30", P.FORA_DO_LEITO)]
    [a] = _tipo(detectar(serie, cfg), "saida_do_leito")
    assert a.severidade is Severidade.MEDIA
    assert a.features["periodo"] == "dia"


def test_queda_sempre_alta(cfg):
    serie = [obs("13:59", P.SUPINO), obs("14:00", P.CHAO)]
    [a] = _tipo(detectar(serie, cfg), "queda")
    assert a.severidade is Severidade.ALTA
    assert a.score == 1.0


def test_queda_duas_fontes_um_evento(cfg):
    serie = [
        obs("02:59", P.SUPINO),
        obs("03:00:01", P.CHAO, fonte="video_pose", queda=True),
        obs("03:00:03", P.CHAO, fonte="video_caixa", queda=True),
    ]
    [a] = _tipo(detectar(serie, cfg), "queda")
    assert a.fontes == ("video_caixa", "video_pose")
    assert (a.inicio, a.fim) == (instante("03:00:01"), instante("03:00:03"))


def test_quedas_distantes_sao_duas(cfg):
    serie = [
        obs("02:00", P.CHAO, queda=True),
        obs("02:30", P.SUPINO),
        obs("03:00", P.CHAO, queda=True),
    ]
    assert len(_tipo(detectar(serie, cfg), "queda")) == 2


def test_filtro_de_tipos(cfg):
    serie = [obs("22:30", P.SUPINO), obs("23:10", P.FORA_DO_LEITO), obs("23:20", P.CHAO)]
    assert {a.event_type for a in detectar(serie, cfg, tipos=["queda"])} == {"queda"}


def test_features_trazem_metricas(cfg):
    serie = [obs("14:00", P.SUPINO), obs("14:30", P.FORA_DO_LEITO)]
    [a] = detectar(serie, cfg)
    for chave in ("minutos_sem_mudanca", "reposicionamentos_por_hora", "saidas_do_leito"):
        assert chave in a.features


# --- baseline ------------------------------------------------------------------


def _alternando(inicio_h: int, horas: int, por_hora: float, posicoes=(P.SUPINO, P.LATERAL_ESQ)):
    """Série a partir de 2026-10-01 00:00 + inicio_h com ``por_hora`` trocas de posição."""
    base = instante("00:00") + timedelta(hours=inicio_h)
    passo = timedelta(hours=1) / por_hora
    return [
        Observacao(base + passo * i, posicoes[i % 2], "teste")
        for i in range(int(horas * por_hora))
    ]


def _parado(inicio_h: int, horas: int, posicao=P.SUPINO):
    base = instante("00:00") + timedelta(hours=inicio_h)
    return [Observacao(base + timedelta(minutes=30) * i, posicao, "teste") for i in range(horas * 2 + 1)]


def test_agitacao_subita(cfg):
    # 24 h com 1 troca a cada 2 h, depois 6 h com 3 trocas/h.
    serie = [*_alternando(0, 24, 0.5), *_alternando(24, 6, 3)]
    [a] = _tipo(detectar(serie, cfg), "agitacao_subita")
    assert a.features["taxa_atual_por_hora"] >= 2 * a.features["taxa_baseline_por_hora"]
    assert a.severidade is Severidade.MEDIA
    assert a.inicio >= instante("00:00") + timedelta(hours=18)


def test_reducao_mobilidade(cfg):
    serie = [*_alternando(0, 24, 1), *_parado(24, 6, posicao=P.LATERAL_ESQ)]
    [a] = _tipo(detectar(serie, cfg), "reducao_mobilidade")
    assert a.features["taxa_atual_por_hora"] <= 0.5 * a.features["taxa_baseline_por_hora"]


def test_padrao_estavel_nao_dispara_baseline(cfg):
    serie = _alternando(0, 36, 1)
    tipos = {a.event_type for a in detectar(serie, cfg)}
    assert not tipos & {"agitacao_subita", "reducao_mobilidade"}


def test_baseline_curta_nao_emite(cfg):
    serie = [*_alternando(0, 8, 0.5), *_alternando(8, 6, 3)]
    tipos = {a.event_type for a in detectar(serie, cfg)}
    assert not tipos & {"agitacao_subita", "reducao_mobilidade"}


def test_reducao_nao_dispara_sem_observacao(cfg):
    # 24 h ativas e depois nada registrado: falta de dado não é redução.
    serie = [*_alternando(0, 24, 1), obs("06:00", P.SUPINO, dia=2)]
    assert _tipo(detectar(serie, cfg), "reducao_mobilidade") == []


def test_serie_vazia(cfg):
    assert detectar([], cfg) == []
