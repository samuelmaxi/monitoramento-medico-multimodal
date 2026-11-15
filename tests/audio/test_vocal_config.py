"""US12/US14: a config dos biomarcadores documenta os limiares e recusa valores inválidos."""

from __future__ import annotations

import copy

import pytest
import yaml

from audio.vocal.config import (
    CLASSES_VOCAIS,
    CRITERIOS,
    ErroDeConfiguracao,
    carregar_config,
    config_de_dict,
)
from contratos import Severidade

from .conftest import RAIZ

CAMINHO = RAIZ / "audio" / "vocal" / "config.yaml"


def _yaml() -> dict:
    return copy.deepcopy(yaml.safe_load(CAMINHO.read_text(encoding="utf-8")))


def test_limiares_da_us12_estao_documentados_no_yaml(cfg_vocal):
    """Jitter > 1,04 %, shimmer > 3,81 % e HNR < 20 dB (normalidade MDVP) vêm da US12."""
    assert cfg_vocal.limiares.jitter_local_pct_max == 1.04
    assert cfg_vocal.limiares.shimmer_local_pct_max == 3.81
    assert cfg_vocal.limiares.hnr_db_min == 20.0
    texto = CAMINHO.read_text(encoding="utf-8")
    assert "[MDVP]" in texto
    assert "EXPERIMENTAL" in texto


def test_as_tres_classes_da_us14_estao_configuradas(cfg_vocal):
    assert set(cfg_vocal.regras) == CLASSES_VOCAIS
    assert cfg_vocal.regras["dificuldade_respiratoria"].severidade_forte is Severidade.ALTA


def test_todo_criterio_do_yaml_existe_no_codigo(cfg_vocal):
    usados = {c for regra in cfg_vocal.regras.values() for c in regra.criterios}
    assert usados <= CRITERIOS


def test_criterios_da_config_do_codigo_e_dos_limiares_sao_o_mesmo_conjunto():
    from audio.vocal.classificacao import CRITERIOS_IMPLEMENTADOS, LIMIARES_DO_CRITERIO

    assert set(CRITERIOS_IMPLEMENTADOS) == CRITERIOS == set(LIMIARES_DO_CRITERIO)


def test_todo_limiar_citado_por_um_criterio_existe_na_config(cfg_vocal):
    from audio.vocal.classificacao import LIMIARES_DO_CRITERIO

    for criterio, chaves in LIMIARES_DO_CRITERIO.items():
        for chave in chaves:
            assert hasattr(cfg_vocal.limiares, chave), (criterio, chave)


def test_classes_respiratorias_apontam_para_nomes_do_audioset(cfg_vocal):
    nomes = {c.classe_audioset for c in cfg_vocal.respiratorio.classes}
    assert {"Cough", "Breathing", "Wheeze", "Gasp"} <= nomes


def test_formatos_com_perdas_incluem_o_opus_do_whatsapp_e_nao_os_sem_perdas(cfg_vocal):
    perdas = cfg_vocal.qualidade.formatos_com_perdas

    assert {"ogg", "opus", "mp3", "m4a", "aac"} <= perdas
    assert not perdas & {"wav", "flac"}


def test_baseline_cobre_as_metricas_dos_biomarcadores(cfg_vocal):
    assert set(cfg_vocal.baseline.metricas) == {
        "jitter_local_pct", "shimmer_local_pct", "hnr_db", "f0_media_hz", "f0_desvio_st",
        "intensidade_db", "proporcao_pausas", "taxa_fala_pps", "taxa_articulacao_pps",
    }


def test_chave_ausente_e_desconhecida_sao_recusadas():
    sem_limiares = _yaml()
    del sem_limiares["limiares"]["hnr_db_min"]
    with pytest.raises(ErroDeConfiguracao, match="hnr_db_min"):
        config_de_dict(sem_limiares)

    extra = _yaml()
    extra["praat"]["inventado"] = 1
    with pytest.raises(ErroDeConfiguracao, match="inventado"):
        config_de_dict(extra)


@pytest.mark.parametrize(
    ("mutacao", "trecho"),
    [
        (lambda d: d["classes"].pop("fadiga_vocal"), "faltam as classes"),
        (lambda d: d["classes"].update(outra={"criterios": ["hnr_baixo"], "minimo": 1,
                                              "severidade": "media"}), "classes desconhecidas"),
        (lambda d: d["classes"]["fadiga_vocal"].update(criterios=["magia"]), "critério desconhecido"),
        (lambda d: d["classes"]["fadiga_vocal"].update(minimo=9), "excede os critérios"),
        (lambda d: d["classes"]["fadiga_vocal"].update(forte=3), "vêm juntos"),
        (lambda d: d["classes"]["possivel_disartria"].update(exigir_um_de=["f0_monotona"]),
         "contido em criterios"),
        (lambda d: d["classes"]["fadiga_vocal"].update(severidade="gravissima"), "gravissima"),
        (lambda d: d["limiares"].update(proporcao_pausas_max=1.5), "(0, 1]"),
        (lambda d: d["limiares"].update(jitter_local_pct_max=-1), "jitter_local_pct_max"),
        (lambda d: d["praat"].update(pitch_ceiling_hz=50), "pitch_ceiling_hz"),
        (lambda d: d["respiratorio"].update(classes={}), "vazio"),
        (lambda d: d["respiratorio"]["classes"]["Cough"].update(severidade="x"), "severidade"),
        (lambda d: d["baseline"].update(audios_maximos=1), "audios_maximos"),
        (lambda d: d["baseline"].update(metricas={}), "vazio"),
        (lambda d: d["pausas"].update(limiar_relativo_db=5), "limiar_relativo_db"),
        (lambda d: d["pausas"].update(nivel_minimo_fala_db=3), "nivel_minimo_fala_db"),
        (lambda d: d["qualidade"].update(formatos_com_perdas="ogg"), "lista não vazia"),
        (lambda d: d["qualidade"].update(extra=1), "extra"),
        (lambda d: d.pop("qualidade"), "qualidade"),
    ],
)
def test_valores_fora_do_dominio_sao_recusados(mutacao, trecho):
    dados = _yaml()
    mutacao(dados)

    with pytest.raises(ErroDeConfiguracao, match=trecho.replace("(", r"\(").replace(")", r"\)")):
        config_de_dict(dados)


def test_config_padrao_carrega_do_disco():
    cfg = carregar_config(CAMINHO)

    assert cfg.fonte == CAMINHO
    assert cfg.versao == "1.0.0"
