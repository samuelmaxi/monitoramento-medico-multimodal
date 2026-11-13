"""US12: comparação com a baseline do próprio paciente, quando há mais de um áudio dele."""

from __future__ import annotations

import json

import pytest

from audio.vocal.baseline import RegistroBaseline, RepositorioBaseline, comparar
from contratos import pseudonimizar_paciente

from .conftest import CHAVE_TESTE

PACIENTE = pseudonimizar_paciente("10000032", chave=CHAVE_TESTE)


def reg(audio_id, **metricas):
    return RegistroBaseline(audio_id, metricas)


def historico(valores, metrica="jitter_local_pct"):
    return [reg(f"a{i}", **{metrica: v}) for i, v in enumerate(valores)]


# --- comparação ----------------------------------------------------------------------------- #


def test_metrica_muito_acima_da_baseline_e_desvio_com_direcao_e_z(cfg_vocal):
    base = historico([0.30, 0.32, 0.29, 0.31, 0.30])

    comparacao = comparar(base, {"jitter_local_pct": 1.50}, cfg_vocal.baseline)

    assert comparacao.suficiente
    assert comparacao.audios == 5
    assert len(comparacao.desvios) == 1
    d = comparacao.desvios[0]
    assert d.metrica == "jitter_local_pct"
    assert d.direcao == "acima"
    assert d.z >= cfg_vocal.baseline.z_minimo
    assert d.mediana == pytest.approx(0.30)
    assert comparacao.medianas["jitter_local_pct"] == pytest.approx(0.30)


def test_metrica_muito_abaixo_tem_direcao_abaixo(cfg_vocal):
    base = historico([80.0, 81.0, 79.5, 80.5], metrica="intensidade_db")

    comparacao = comparar(base, {"intensidade_db": 60.0}, cfg_vocal.baseline)

    assert comparacao.desvios[0].direcao == "abaixo"
    assert comparacao.desvios[0].z < 0


def test_variacao_normal_nao_e_desvio(cfg_vocal):
    base = historico([0.30, 0.32, 0.29, 0.31, 0.30])

    assert comparar(base, {"jitter_local_pct": 0.33}, cfg_vocal.baseline).desvios == ()


def test_piso_da_dispersao_evita_z_enorme_com_historico_constante(cfg_vocal):
    """Histórico idêntico dá MAD 0; sem piso, qualquer diferença mínima seria um desvio absurdo."""
    base = historico([0.30] * 5)
    piso = cfg_vocal.baseline.metricas["jitter_local_pct"]

    pequena = comparar(base, {"jitter_local_pct": 0.30 + 2 * piso}, cfg_vocal.baseline)
    grande = comparar(base, {"jitter_local_pct": 0.30 + 5 * piso}, cfg_vocal.baseline)

    assert pequena.desvios == ()
    assert len(grande.desvios) == 1
    assert grande.desvios[0].dispersao == pytest.approx(piso)


def test_poucos_audios_nao_formam_baseline(cfg_vocal):
    minimo = cfg_vocal.baseline.audios_minimos
    base = historico([0.30] * (minimo - 1))

    comparacao = comparar(base, {"jitter_local_pct": 5.0}, cfg_vocal.baseline)

    assert not comparacao.suficiente
    assert comparacao.desvios == ()
    assert comparacao.medianas == {}


def test_o_proprio_audio_nao_entra_na_sua_baseline(cfg_vocal):
    base = [*historico([0.30, 0.31, 0.29, 0.30]), reg("este", jitter_local_pct=5.0)]

    comparacao = comparar(base, {"jitter_local_pct": 5.0}, cfg_vocal.baseline,
                          ignorar_audio_id="este")

    assert comparacao.audios == 4
    assert len(comparacao.desvios) == 1


def test_metrica_ausente_no_historico_ou_no_audio_e_ignorada(cfg_vocal):
    base = [reg(f"a{i}", jitter_local_pct=0.3, hnr_db=None) for i in range(5)]

    comparacao = comparar(base, {"jitter_local_pct": None, "hnr_db": 10.0}, cfg_vocal.baseline)

    assert comparacao.desvios == ()
    assert "hnr_db" not in comparacao.medianas  # nenhum valor no histórico
    assert "jitter_local_pct" in comparacao.medianas


def test_desvios_vem_ordenados_do_maior_z_para_o_menor(cfg_vocal):
    base = [reg(f"a{i}", jitter_local_pct=0.3, hnr_db=25.0) for i in range(5)]

    comparacao = comparar(base, {"jitter_local_pct": 1.0, "hnr_db": 5.0}, cfg_vocal.baseline)

    zs = [abs(d.z) for d in comparacao.desvios]
    assert zs == sorted(zs, reverse=True)
    assert len(zs) == 2


def test_comparacao_serializa_para_o_relatorio(cfg_vocal):
    base = historico([0.30, 0.32, 0.29, 0.31, 0.30])

    dados = comparar(base, {"jitter_local_pct": 1.5}, cfg_vocal.baseline).para_dict()

    json.dumps(dados, allow_nan=False)
    assert dados["desvios"][0]["direcao"] == "acima"


# --- repositório ---------------------------------------------------------------------------- #


def test_registrar_e_carregar_preservam_a_ordem(tmp_path):
    repo = RepositorioBaseline(tmp_path / "baseline")

    repo.registrar(PACIENTE, "a1", {"hnr_db": 25.0}, maximo=10)
    repo.registrar(PACIENTE, "a2", {"hnr_db": 24.0, "jitter_local_pct": None}, maximo=10)

    historico_ = repo.carregar(PACIENTE)
    assert [r.audio_id for r in historico_] == ["a1", "a2"]
    assert historico_[1].metricas["jitter_local_pct"] is None


def test_registrar_o_mesmo_audio_de_novo_substitui_em_vez_de_duplicar(tmp_path):
    repo = RepositorioBaseline(tmp_path)

    repo.registrar(PACIENTE, "a1", {"hnr_db": 25.0}, maximo=10)
    repo.registrar(PACIENTE, "a1", {"hnr_db": 18.0}, maximo=10)

    historico_ = repo.carregar(PACIENTE)
    assert len(historico_) == 1
    assert historico_[0].metricas["hnr_db"] == 18.0


def test_historico_e_limitado_aos_audios_mais_recentes(tmp_path):
    repo = RepositorioBaseline(tmp_path)

    for i in range(6):
        repo.registrar(PACIENTE, f"a{i}", {"hnr_db": float(i)}, maximo=3)

    assert [r.audio_id for r in repo.carregar(PACIENTE)] == ["a3", "a4", "a5"]


def test_paciente_sem_historico_devolve_lista_vazia(tmp_path):
    assert RepositorioBaseline(tmp_path).carregar(PACIENTE) == []


@pytest.mark.parametrize("identificador", ["10000032", "../fora", "pt_xyz", "pt_" + "g" * 24])
def test_so_o_pseudonimo_vira_nome_de_arquivo(tmp_path, identificador):
    repo = RepositorioBaseline(tmp_path)

    with pytest.raises(ValueError, match="pseudônimo"):
        repo.registrar(identificador, "a1", {}, maximo=3)
    with pytest.raises(ValueError, match="pseudônimo"):
        repo.carregar(identificador)


def test_pacientes_diferentes_tem_arquivos_diferentes(tmp_path):
    outro = pseudonimizar_paciente("20000001", chave=CHAVE_TESTE)
    repo = RepositorioBaseline(tmp_path)

    repo.registrar(PACIENTE, "a1", {"hnr_db": 25.0}, maximo=5)
    repo.registrar(outro, "b1", {"hnr_db": 10.0}, maximo=5)

    assert [r.audio_id for r in repo.carregar(PACIENTE)] == ["a1"]
    assert [r.audio_id for r in repo.carregar(outro)] == ["b1"]
    assert sorted(p.name for p in tmp_path.glob("*.json")) == sorted(
        [f"{PACIENTE}.json", f"{outro}.json"])
