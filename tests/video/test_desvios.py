"""Testes da US08 (desvios do padrão esperado), com séries e poses sintéticas."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from contratos import SEM_ACHADOS, Contexto, EventoAchado, Evidencia, Modalidade, Severidade
from video import criar_video_teste
from video.desvios import MODEL_VERSION
from video.desvios.areas import desvios_de_areas
from video.desvios.avaliacao import Rotulo, avaliar
from video.desvios.config import (
    ErroDeConfiguracao,
    Faixa,
    RegraRepeticoes,
    RegraSalto,
    carregar_config,
    config_de_dict,
)
from video.desvios.eventos import ContextoEmissao, montar_evento, montar_sem_achados, relatorio
from video.desvios.regras import (
    ASSIMETRIA,
    POSTURA,
    analisar,
    desvios_de_faixa,
    desvios_de_mudanca_brusca,
    desvios_de_queda,
    repeticoes,
)
from video.desvios.run import executar
from video.desvios.sinais import saltos_mantidos, trechos
from video.pose.angles import ANGLE_COLUMNS
from video.pose.sequence import PoseFrame, PoseSequence

from .conftest import PACIENTE

RAIZ = Path(__file__).resolve().parents[2]
CONFIG = RAIZ / "config" / "us08_padroes.yaml"
CFG = carregar_config(CONFIG)
GERAL = CFG.geral
INICIO = datetime(2026, 10, 5, 19, 0, tzinfo=UTC)


def _tempos(n: int, fps: float = 10.0) -> np.ndarray:
    return np.arange(n) / fps


def _faixa(**campos) -> Faixa:
    padrao = {
        "coluna": "inclinacao_tronco",
        "min": None,
        "max": 30.0,
        "janela_s": 1.0,
        "severidade": Severidade.BAIXA,
    }
    return Faixa(**{**padrao, **campos})


# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #


def _dados_config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_config_versionada_tem_os_perfis_por_tipo_de_video():
    assert CFG.versao
    tipos = {p.tipo_video for p in CFG.perfis.values()}
    assert tipos == {"fisioterapia", "leito", "cirurgia"}
    abducao = CFG.perfil("fisioterapia_abducao_ombro_frontal")
    assert abducao.repeticoes.alvo is None and abducao.repeticoes.esperadas == (5, 10)
    assert abducao.repeticoes.referencia.repeticoes == 3
    assert CFG.perfil("internacao_leito").queda.severidade is Severidade.ALTA
    assert CFG.perfil("cirurgia").areas


def test_regra_sem_janela_usa_a_janela_minima_geral():
    faixa = next(
        f for f in CFG.perfil("fisioterapia_geral").faixas if f.coluna == "inclinacao_tronco"
    )
    assert faixa.janela_s == GERAL.janela_minima_s


@pytest.mark.parametrize(
    ("caminho", "valor", "trecho_do_erro"),
    [
        (("perfis", "cirurgia", "areas", "area_restrita", "janela"), 1.0, "chaves desconhecidas"),
        (
            ("perfis", "fisioterapia_geral", "faixas", "inclinacao_tronco", "severidade"),
            "grave",
            "severidade",
        ),
        (
            ("perfis", "fisioterapia_abducao_ombro_frontal", "repeticoes", "alvo"),
            40,
            "além do repouso",
        ),
        (
            ("perfis", "fisioterapia_geral", "faixas", "tornozelo"),
            {"max": 10},
            "coluna desconhecida",
        ),
        (("geral", "fracao_permanencia"), 1.5, "fracao_permanencia"),
    ],
)
def test_config_invalida_falha_na_carga(caminho, valor, trecho_do_erro):
    dados = copy.deepcopy(_dados_config())
    alvo = dados
    for chave in caminho[:-1]:
        alvo = alvo[chave]
    alvo[caminho[-1]] = valor
    with pytest.raises(ErroDeConfiguracao, match=trecho_do_erro):
        config_de_dict(dados)


def test_config_sem_versao_falha():
    dados = _dados_config()
    del dados["versao"]
    with pytest.raises(ErroDeConfiguracao, match="versao"):
        config_de_dict(dados)


def test_perfil_inexistente_lista_os_disponiveis():
    with pytest.raises(ErroDeConfiguracao, match=r"Disponíveis: .*cirurgia"):
        CFG.perfil("ortopedia")


# --------------------------------------------------------------------------- #
# Sinais
# --------------------------------------------------------------------------- #


def test_trechos_exige_persistencia_minima():
    t = _tempos(30)
    curto = np.zeros(30, bool)
    curto[5:14] = True  # 0,9 s
    longo = np.zeros(30, bool)
    longo[5:15] = True  # 1,0 s
    assert trechos(t, curto, janela_s=1.0, lacuna_max_s=0.0) == []
    assert trechos(t, longo, janela_s=1.0, lacuna_max_s=0.0) == [(5, 14)]


def test_trechos_junta_lacuna_curta_e_separa_lacuna_longa():
    t = _tempos(40)
    mascara = np.zeros(40, bool)
    mascara[0:6] = mascara[8:14] = True  # lacuna de 2 quadros (0,2 s)
    mascara[20:26] = True  # lacuna de 6 quadros
    assert trechos(t, mascara, janela_s=1.0, lacuna_max_s=0.3) == [(0, 13)]


def test_salto_mantido_versus_movimento_que_volta_ou_lento():
    t = _tempos(40)
    queda = np.r_[np.zeros(10), np.linspace(0, 1.2, 5), np.full(25, 1.2)]
    volta = np.r_[np.zeros(10), np.linspace(0, 1.2, 5), np.zeros(25)]
    lento = np.linspace(0, 1.2, 40)
    kwargs = {
        "limiar": 0.8,
        "tempo_max_s": 1.0,
        "janela_s": 0.5,
        "lacuna_max_s": 0.3,
        "fracao_permanencia": 0.7,
        "sinal": 1,
    }
    (salto,) = saltos_mantidos(t, queda, **kwargs)
    assert salto.variacao == pytest.approx(1.2) and t[salto.ref] < t[salto.impacto]
    assert saltos_mantidos(t, volta, **kwargs) == []
    assert saltos_mantidos(t, lento, **kwargs) == []


def test_salto_no_fim_do_video_sem_completar_a_janela_nao_conta():
    t = _tempos(14)
    serie = np.r_[np.zeros(10), [0.4, 0.9, 1.2, 1.2]]
    assert (
        saltos_mantidos(
            t,
            serie,
            limiar=0.8,
            tempo_max_s=1.0,
            janela_s=0.5,
            lacuna_max_s=0.3,
            fracao_permanencia=0.7,
            sinal=1,
        )
        == []
    )


# --------------------------------------------------------------------------- #
# Regras posturais
# --------------------------------------------------------------------------- #


def test_faixa_dispara_so_quando_persiste_e_cita_valor_limiar_e_tempo():
    t = _tempos(50)
    frames = np.arange(50) * 3
    breve = np.r_[np.full(10, 5.0), np.full(9, 45.0), np.full(31, 5.0)]
    mantido = np.r_[np.full(10, 5.0), np.full(14, 40.0), [50.0], np.full(25, 5.0)]
    assert desvios_de_faixa(t, frames, breve, _faixa(), GERAL, POSTURA) == []
    (d,) = desvios_de_faixa(t, frames, mantido, _faixa(), GERAL, POSTURA)
    assert d.event_type == POSTURA and d.regra == "faixa.inclinacao_tronco"
    assert (d.inicio_s, d.fim_s, d.pico_s, d.frame_idx) == pytest.approx((1.0, 2.5, 2.4, 72))
    assert d.valor == 50 and d.limiar == 30
    assert d.score == pytest.approx(0.5 + 0.5 * 20 / GERAL.score.saturacao_graus, abs=1e-4)
    assert "acima de 30°" in d.resumo and "por 1,5 s" in d.resumo and "aos 2,4 s" in d.resumo


def test_faixa_com_minimo_e_quadro_sem_medida_dentro_da_lacuna():
    t = _tempos(30)
    joelho = np.full(30, 170.0)
    joelho[5:20] = 30.0
    joelho[12] = np.nan
    (d,) = desvios_de_faixa(
        t,
        np.arange(30),
        joelho,
        _faixa(coluna="joelho_direito", min=45.0, max=None),
        GERAL,
        POSTURA,
    )
    assert "abaixo de 45°" in d.resumo and d.duracao_s == pytest.approx(1.5)


def test_assimetria_gera_evento_proprio():
    t = _tempos(30)
    assimetria = np.r_[np.full(5, 3.0), np.full(15, 35.0), np.full(10, 3.0)]
    (d,) = desvios_de_faixa(
        t,
        np.arange(30),
        assimetria,
        _faixa(coluna="assimetria_joelho", max=25.0),
        GERAL,
        ASSIMETRIA,
    )
    assert d.event_type == ASSIMETRIA and d.regra == "assimetria.assimetria_joelho"
    assert "joelhos esquerdo e direito" in d.resumo


def _abducoes(picos: list[float], *, repouso_inicial: int = 5) -> np.ndarray:
    serie = [np.full(repouso_inicial, 20.0)]
    for pico in picos:
        subida = np.linspace(20.0, pico, 10)
        serie += [subida, subida[::-1], np.full(5, 20.0)]
    return np.concatenate(serie)


def test_repeticoes_contadas_e_amplitude_insuficiente_vira_postura():
    regra = RegraRepeticoes(
        angulo="ombro_direito",
        sentido="sobe",
        repouso=60,
        alvo=130,
        esperadas=(2, 3),
        duracao_min_s=1.0,
        severidade=Severidade.BAIXA,
    )
    x = _abducoes([135, 125, 138])
    resumo, desvios = repeticoes(
        _tempos(len(x)), np.arange(len(x)), pd.DataFrame({"ombro_direito": x}), regra, GERAL
    )
    assert resumo.contadas == 3 and resumo.dentro_do_esperado
    assert [r.atingiu_alvo for r in resumo.repeticoes] == [True, False, True]
    (d,) = desvios
    assert d.event_type == POSTURA and d.regra == "repeticao.ombro_direito"
    assert (
        d.valor == pytest.approx(125)
        and "repetição 2" in d.resumo
        and "abaixo do alvo de 130°" in d.resumo
    )


def test_repeticao_cortada_no_inicio_do_video_nao_e_julgada():
    regra = RegraRepeticoes(
        angulo="ombro_direito",
        sentido="sobe",
        repouso=60,
        alvo=130,
        esperadas=None,
        duracao_min_s=1.0,
        severidade=Severidade.BAIXA,
    )
    x = _abducoes([100, 135], repouso_inicial=0)[6:]  # vídeo começa com o braço acima do repouso
    resumo, desvios = repeticoes(
        _tempos(len(x)), np.arange(len(x)), pd.DataFrame({"ombro_direito": x}), regra, GERAL
    )
    assert resumo.contadas == 1 and desvios == []


def test_repeticao_comparada_com_a_serie_de_referencia_do_paciente():
    regra = CFG.perfil("fisioterapia_abducao_ombro_frontal").repeticoes
    ombro = _abducoes([130, 132, 128, 131, 112, 129])
    cotovelo = np.full(len(ombro), 170.0)
    quarta = slice(5 + 3 * 25, 5 + 4 * 25)  # 4ª repetição: cotovelo dobrado
    cotovelo[quarta] = 150.0
    angulos = pd.DataFrame({"ombro_direito": ombro, "cotovelo_direito": cotovelo})
    resumo, desvios = repeticoes(_tempos(len(ombro)), np.arange(len(ombro)), angulos, regra, GERAL)
    assert [r.referencia for r in resumo.repeticoes] == [True] * 3 + [False] * 3
    assert resumo.referencia == {"ombro_direito": 130.0, "cotovelo_direito": 170.0}
    assert [(d.features["repeticao"], d.medida) for d in desvios] == [
        (4, "cotovelo_direito"),
        (5, "ombro_direito"),
    ]
    quarta_rep, quinta_rep = desvios
    assert quarta_rep.regra == "repeticao.referencia" and quarta_rep.valor == 150.0
    assert "referência 170°, tolerância ±10°" in quarta_rep.resumo
    assert quinta_rep.features["diferenca_ombro_direito"] == -18.0


def _corpo(quadril_y: float, *, x: float = 300.0, deitado: bool = False) -> dict:
    """Esqueleto BODY_25 parcial (10 keypoints) com tronco de 100 px."""
    pescoco = [x + 100, quadril_y, 0.9] if deitado else [x, quadril_y - 100, 0.9]
    kp = {
        "Neck": pescoco,
        "MidHip": [x, quadril_y, 0.9],
        "LHip": [x - 10, quadril_y, 0.9],
        "RHip": [x + 10, quadril_y, 0.9],
        "LKnee": [x - 10, quadril_y + 50, 0.9],
        "RKnee": [x + 10, quadril_y + 50, 0.9],
        "LAnkle": [x - 10, quadril_y + 100, 0.9],
        "RAnkle": [x + 10, quadril_y + 100, 0.9],
        "LShoulder": [pescoco[0] - 20, pescoco[1], 0.9],
        "RShoulder": [pescoco[0] + 20, pescoco[1], 0.9],
    }
    return kp


def _pose(corpos: list[dict], *, fps: float = 10.0, origem: str = "video.avi") -> PoseSequence:
    return PoseSequence(
        video_id="sintetico",
        source_path=origem,
        keypoint_names=sorted(corpos[0]),
        metadata={
            "min_valid_keypoints": 8,
            "model": "BODY_25",
            "module_version": "0.1.0",
            "source_fps": fps,
            "processed_fps": fps,
        },
        frames=[PoseFrame(i, round(1000 * i / fps), kp) for i, kp in enumerate(corpos)],
    )


REGRA_QUEDA = RegraSalto(limiar=0.8, tempo_max_s=1.0, janela_s=0.5, severidade=Severidade.ALTA)


def test_queda_quando_o_quadril_desce_rapido_e_fica_embaixo():
    alturas = [200.0] * 10 + [230, 270, 310, 320] + [320.0] * 10
    (d,) = desvios_de_queda(_pose([_corpo(y) for y in alturas]), REGRA_QUEDA, GERAL)
    assert d.event_type == "mudanca_brusca_ou_queda" and d.regra == "queda"
    assert d.severidade is Severidade.ALTA and d.valor == pytest.approx(1.2)
    assert d.features["tronco_px"] == pytest.approx(100.0) and "Possível queda" in d.resumo


def test_deitar_devagar_ou_agachar_e_levantar_nao_e_queda():
    devagar = [200.0 + 4 * i for i in range(40)]
    agacha = [200.0] * 10 + [260, 320, 320, 260] + [200.0] * 10
    assert desvios_de_queda(_pose([_corpo(y) for y in devagar]), REGRA_QUEDA, GERAL) == []
    assert desvios_de_queda(_pose([_corpo(y) for y in agacha]), REGRA_QUEDA, GERAL) == []


def test_mudanca_brusca_do_tronco_e_descartada_quando_ja_ha_queda():
    perfil = CFG.perfil("internacao_leito")
    corpos = (
        [_corpo(200.0)] * 10
        + [_corpo(y, deitado=True) for y in (260, 320, 320)]
        + [_corpo(320.0, deitado=True)] * 10
    )
    tronco = np.r_[np.zeros(10), np.full(13, 90.0)]
    assert desvios_de_mudanca_brusca(
        _tempos(23), np.arange(23), tronco, perfil.mudanca_brusca, GERAL
    )
    resultado = analisar(perfil, CFG, pose=_pose(corpos))
    assert [d.regra for d in resultado.desvios] == ["queda"]


def _angulos_neutros(n: int, **colunas) -> pd.DataFrame:
    df = pd.DataFrame({"frame_idx": range(n), "timestamp_ms": range(0, 100 * n, 100)})
    for coluna in ANGLE_COLUMNS:
        df[coluna] = colunas.get(
            coluna, 2.0 if coluna.startswith(("assimetria", "inclinacao")) else 170.0
        )
    return df


def test_analisar_ordena_desvios_e_avisa_repeticoes_fora_do_esperado():
    perfil = CFG.perfil("fisioterapia_abducao_ombro_frontal")
    ombro = _abducoes([130, 131, 129, 100])
    n = len(ombro)
    tronco = np.r_[np.full(10, 2.0), np.full(15, 40.0), np.full(n - 25, 2.0)]
    pose = _pose([_corpo(200.0)] * n)
    resultado = analisar(
        perfil,
        CFG,
        pose=pose,
        angulos=_angulos_neutros(n, ombro_direito=ombro, inclinacao_tronco=tronco),
    )
    assert [d.regra for d in resultado.desvios] == [
        "faixa.inclinacao_tronco",
        "repeticao.referencia",
    ]
    assert resultado.repeticoes.contadas == 4 and resultado.confiavel
    assert any("contadas 4 repetições" in a for a in resultado.avisos)


def test_analisar_avisa_quando_nao_ha_repeticoes_para_a_referencia():
    perfil = CFG.perfil("fisioterapia_abducao_ombro_frontal")
    ombro = _abducoes([130, 100])
    n = len(ombro)
    resultado = analisar(
        perfil,
        CFG,
        pose=_pose([_corpo(200.0)] * n),
        angulos=_angulos_neutros(n, ombro_direito=ombro),
    )
    assert resultado.desvios == () and resultado.repeticoes.referencia is None
    assert any("precisa de mais de 3" in a for a in resultado.avisos)


def test_analisar_exige_pose_para_perfil_postural():
    with pytest.raises(ValueError, match="precisa da pose"):
        analisar(CFG.perfil("fisioterapia_geral"), CFG)


def test_perfil_misto_sem_pose_avalia_so_as_areas_e_nao_se_declara_confiavel():
    eventos = [
        _evento_area("entrada_area_critica", 2.0, area="borda_leito"),
        _evento_area("saida_area_critica", 6.0, area="borda_leito"),
    ]
    resultado = analisar(CFG.perfil("internacao_leito"), CFG, eventos_areas=eventos)
    assert [d.regra for d in resultado.desvios] == ["area.borda_leito"]
    assert not resultado.confiavel and "Sem pose" in resultado.avisos[0]


# --------------------------------------------------------------------------- #
# Áreas críticas (eventos da US07)
# --------------------------------------------------------------------------- #


def _evento_area(
    tipo: str,
    t: float,
    *,
    classe: str = "person",
    area: str = "area_restrita",
    track: int | None = 3,
) -> EventoAchado:
    features = {
        "classe": classe,
        "area_critica_id": area,
        "area_critica_nome": "Área restrita",
        "tempo_video_s": t,
        "frame_index": int(t * 10),
        "x1": 10.0,
        "y1": 10.0,
        "x2": 50.0,
        "y2": 90.0,
    }
    if track is not None:
        features["track_id"] = track
    return EventoAchado.criar(
        patient_id=PACIENTE,
        modality=Modalidade.VIDEO,
        event_type=tipo,
        timestamp=INICIO + timedelta(seconds=t),
        score=0.8,
        severity=Severidade.BAIXA,
        evidence=Evidencia(summary=f"{classe} {tipo}", features=features),
        model_version="video-areas-criticas@0.1.0",
    )


def test_presenca_indevida_pareia_entrada_e_saida_e_cita_os_eventos_de_origem():
    regras = CFG.perfil("cirurgia").areas
    entrada, saida = (
        _evento_area("entrada_area_critica", 2.0),
        _evento_area("saida_area_critica", 4.5),
    )
    (d,) = desvios_de_areas([saida, entrada], regras, GERAL)
    assert d.event_type == "presenca_indevida_area_critica" and d.severidade is Severidade.ALTA
    assert (d.inicio_s, d.fim_s, d.valor) == (2.0, 4.5, 2.5)
    assert d.relacionados == (str(entrada.event_id), str(saida.event_id))
    assert d.inicio_utc == entrada.timestamp and d.fim_utc == saida.timestamp
    assert "por 2,5 s" in d.resumo


def test_presenca_curta_classe_permitida_ou_area_sem_regra_nao_disparam():
    regras = CFG.perfil("cirurgia").areas
    curta = [_evento_area("entrada_area_critica", 2.0), _evento_area("saida_area_critica", 2.5)]
    permitida = [
        _evento_area("entrada_area_critica", 2.0, classe="chair"),
        _evento_area("saida_area_critica", 9.0, classe="chair"),
    ]
    outra = [
        _evento_area("entrada_area_critica", 2.0, area="porta"),
        _evento_area("saida_area_critica", 9.0, area="porta"),
    ]
    assert desvios_de_areas(curta + permitida + outra, regras, GERAL) == []


def test_presenca_sem_saida_vai_ate_o_fim_do_video():
    regras = CFG.perfil("cirurgia").areas
    (d,) = desvios_de_areas(
        [_evento_area("entrada_area_critica", 2.0, track=None)], regras, GERAL, fim_video_s=10.0
    )
    assert d.fim_s == 10.0 and d.features["saida_observada"] is False and "fim do vídeo" in d.resumo


# --------------------------------------------------------------------------- #
# Contrato da US04
# --------------------------------------------------------------------------- #

CTX = ContextoEmissao(
    patient_id=PACIENTE,
    inicio_utc=INICIO,
    contexto=Contexto(source_id="PM_001", procedure_type="fisioterapia"),
    modelos={"openpose": "BODY_25@0.1.0"},
)


def test_evento_do_desvio_respeita_o_contrato_e_carrega_limiares_versionados():
    perfil = CFG.perfil("fisioterapia_geral")
    t = _tempos(30)
    (d,) = desvios_de_faixa(
        t,
        np.arange(30) * 3,
        np.r_[np.full(5, 2.0), np.full(15, 45.0), np.full(10, 2.0)],
        _faixa(),
        GERAL,
        POSTURA,
    )
    evento = montar_evento(d, CTX, perfil=perfil, cfg=CFG)
    assert evento.event_type == "postura_fora_da_faixa" and evento.model_version == MODEL_VERSION
    assert evento.timestamp == INICIO + timedelta(seconds=0.5)
    assert evento.window.end == INICIO + timedelta(seconds=2.0)
    assert evento.evidence.thresholds["versao_limiares"] == CFG.versao
    assert evento.evidence.thresholds["janela_minima_s"] == 1.0
    assert (
        evento.evidence.features["frame_index"] == 15
        and evento.evidence.features["perfil"] == perfil.id
    )
    assert EventoAchado.de_json(evento.para_json()).para_dict() == evento.para_dict()


def test_presenca_usa_os_instantes_da_us07_e_related_event_ids():
    perfil = CFG.perfil("cirurgia")
    entrada, saida = (
        _evento_area("entrada_area_critica", 2.0),
        _evento_area("saida_area_critica", 4.5),
    )
    (d,) = desvios_de_areas([entrada, saida], perfil.areas, GERAL)
    evento = montar_evento(d, CTX, perfil=perfil, cfg=CFG)
    assert evento.timestamp == entrada.timestamp and evento.window.end == saida.timestamp
    assert evento.related_event_ids == [entrada.event_id, saida.event_id]


def test_sem_achados_so_com_analise_confiavel():
    perfil = CFG.perfil("fisioterapia_geral")
    n = 30
    ok = analisar(perfil, CFG, pose=_pose([_corpo(200.0)] * n), angulos=_angulos_neutros(n))
    evento = montar_sem_achados(ok, CTX, perfil=perfil, cfg=CFG)
    assert evento.event_type == SEM_ACHADOS and evento.severity is Severidade.INFO
    vazia = _pose([{"Neck": [1, 1, 0.9]}] * n)
    ruim = analisar(perfil, CFG, pose=vazia, angulos=_angulos_neutros(n))
    assert not ruim.confiavel and ruim.avisos
    with pytest.raises(ValueError, match="pouco confiável"):
        montar_sem_achados(ruim, CTX, perfil=perfil, cfg=CFG)
    rel = relatorio(ruim, [], perfil=perfil, cfg=CFG, fonte={"source_id": "x"})
    assert rel["confiavel"] is False and rel["total_desvios"] == 0


# --------------------------------------------------------------------------- #
# Avaliação
# --------------------------------------------------------------------------- #


def _desvio_em(inicio: float, fim: float, tipo: str = POSTURA):
    t = _tempos(200)
    valores = np.where((t >= inicio) & (t < fim), 50.0, 2.0)
    (d,) = desvios_de_faixa(t, np.arange(200), valores, _faixa(janela_s=0.1), GERAL, tipo)
    return d


def test_avaliacao_conta_eventos_por_unidade_rotulada():
    rotulos = [
        Rotulo("r1", 0, 4, False),
        Rotulo("r2", 4, 8, True),
        Rotulo("r3", 8, 12, True),
        Rotulo("r4", 14, 18, False),
    ]
    desvios = [
        _desvio_em(5, 6),
        _desvio_em(6.5, 7.5),
        _desvio_em(12.5, 13.5),
        _desvio_em(15, 16),
        _desvio_em(19.5, 19.9),
    ]
    c = avaliar(desvios, rotulos)
    # 4 eventos no intervalo rotulado (o de 19,5 s fica fora): 2 em r2, 1 entre unidades, 1 em r4
    assert (c.eventos, c.eventos_corretos) == (4, 2)
    assert (c.unidades_desvio, c.unidades_desvio_detectadas) == (2, 1)
    assert c.precisao == 0.5 and c.recall == 0.5
    assert c.perdidas == ["r3"] and c.alarmes_falsos == ["fora@12.5s", "r4"]


def test_avaliacao_filtra_por_tipo():
    rotulos = [Rotulo("clipe", 0, 5, True)]
    c = avaliar([_desvio_em(1, 2, ASSIMETRIA)], rotulos, tipos={"mudanca_brusca_ou_queda"})
    assert c.eventos == 0 and c.recall == 0.0


# --------------------------------------------------------------------------- #
# Ponta a ponta (CLI sem OpenPose: pose sintética + vídeo sintético)
# --------------------------------------------------------------------------- #


def test_executar_grava_eventos_relatorio_e_quadro_de_evidencia(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    video = criar_video_teste(tmp_path / "leito.avi", quadros=24, largura=640, altura=480)
    alturas = [200.0] * 10 + [230, 270, 310, 320] + [320.0] * 10
    pose = _pose([_corpo(y) for y in alturas], origem=str(video))
    pose.save_json(tmp_path / "leito.json")

    execucao = executar(
        perfil_id="internacao_leito",
        config=CONFIG,
        pose_path=tmp_path / "leito.json",
        patient_id=PACIENTE,
        inicio_utc="2026-10-05T19:00:00Z",
        saida=Path("saida"),
        pose_config=RAIZ / "video" / "pose" / "config.yaml",
    )

    (evento,) = execucao.eventos
    assert evento.event_type == "mudanca_brusca_ou_queda" and evento.severity is Severidade.ALTA
    (artefato,) = evento.evidence.artifacts
    assert artefato.kind == "frame" and artefato.uri.startswith("saida/sintetico/")
    assert (tmp_path / artefato.uri).is_file()
    linhas = execucao.caminho_eventos.read_text(encoding="utf-8").splitlines()
    assert [EventoAchado.de_json(linha).event_id for linha in linhas] == [evento.event_id]
    rel = json.loads(execucao.caminho_relatorio.read_text(encoding="utf-8"))
    assert rel["total_desvios"] == 1 and rel["desvios"][0]["evidencia"] == [artefato.uri]
    assert rel["versao_limiares"] == CFG.versao

    # Reexecutar sobrescreve, não acumula.
    executar(
        perfil_id="internacao_leito",
        config=CONFIG,
        pose_path=tmp_path / "leito.json",
        patient_id=PACIENTE,
        saida=Path("saida"),
        evidencias=False,
    )
    assert len(execucao.caminho_eventos.read_text(encoding="utf-8").splitlines()) == 1


def test_evidencia_tarja_a_cabeca_pelos_keypoints():
    from video.desvios.evidencia import _tarjar_cabeca

    imagem = np.full((480, 640, 3), 200, np.uint8)
    keypoints = {
        "Nose": [320, 100, 0.9],
        "LEye": [330, 95, 0.9],
        "REye": [310, 95, 0.9],
        "Neck": [320, 160, 0.9],
    }
    _tarjar_cabeca(imagem, keypoints)
    assert (imagem[100, 320] == 0).all() and (imagem[70, 320] == 0).all()
    assert (imagem[300, 320] == 200).all()


def test_queda_seguida_de_oclusao_ainda_conta_mas_so_com_ausencia_tolerada():
    t = _tempos(25)
    serie = np.r_[np.zeros(10), [0.5, 1.0, 1.2], np.full(6, np.nan), [1.2], np.full(5, np.nan)]
    kwargs = {
        "limiar": 0.8,
        "tempo_max_s": 1.0,
        "janela_s": 0.5,
        "lacuna_max_s": 0.3,
        "fracao_permanencia": 0.7,
        "sinal": 1,
    }
    assert saltos_mantidos(t, serie, **kwargs) == []
    (salto,) = saltos_mantidos(t, serie, **kwargs, ausencia_mantem=True)
    assert salto.fim == 19  # termina na última medida válida, não no fim do vídeo
    voltou = serie.copy()
    voltou[19] = 0.1
    assert saltos_mantidos(t, voltou, **kwargs, ausencia_mantem=True) == []
