"""Replay do MIMIC-IV Demo na etapa de sinais vitais (US20, DoD 3)."""

import gzip
import json
from datetime import UTC, datetime, timedelta

import pytest

from pipeline import ConfigPipeline, OrquestradorPipeline
from pipeline.etapas.sinais import carregar_replay_mimic
from sinais import mimic

PACIENTE = "pt_8e5b4d1cfc60a16434dc03f0"


@pytest.fixture
def chartevents(tmp_path):
    """CSV mínimo no formato do MIMIC: subject 1 com 3 medições de FC (2 h de intervalo), subject 2 com 1."""
    linhas = [
        "subject_id,charttime,itemid,valuenum",
        "1,2150-01-01 00:00:00,220045,70",
        "1,2150-01-01 02:00:00,220045,80",
        "1,2150-01-01 04:00:00,220045,90",
        "2,2160-05-05 00:00:00,220045,60",
    ]
    caminho = tmp_path / "chartevents.csv.gz"
    with gzip.open(caminho, "wt", encoding="utf-8") as f:
        f.write("\n".join(linhas) + "\n")
    return caminho


def test_replay_fica_com_o_trecho_final_e_termina_no_instante_pedido(chartevents):
    fim = datetime(2026, 1, 1, 12, tzinfo=UTC)
    medicoes = carregar_replay_mimic(chartevents, 1, 3.0, PACIENTE, fim)
    assert [m.valor for m in medicoes] == [80.0, 90.0]
    assert medicoes[-1].instante == fim
    assert medicoes[-1].instante - medicoes[0].instante == timedelta(hours=2)
    assert {m.paciente for m in medicoes} == {PACIENTE}


def test_replay_de_paciente_inexistente_falha_com_mensagem_clara(chartevents):
    with pytest.raises(ValueError, match="subject_id 99"):
        carregar_replay_mimic(chartevents, 99, 3.0, PACIENTE, datetime.now(UTC))


def test_fonte_mimic_sem_arquivo_registra_falha_em_vez_de_cair_no_sintetico(tmp_path):
    config = ConfigPipeline.from_env(
        fator_velocidade=0.0, saida_dir=tmp_path, sinais_fonte="mimic", mimic_path=tmp_path / "nao_existe.gz"
    )
    resumo = OrquestradorPipeline(config).executar()
    relatorio = json.loads(resumo.caminho_relatorio.read_text(encoding="utf-8"))
    sinais = next(e for e in relatorio["etapas"] if e["etapa"] == "sinais_vitais")
    assert resumo.etapas_com_falha == ("sinais_vitais",)
    assert sinais["status"] == "falha"
    assert "baixar_sinais" in sinais["detalhes"]["erro"]
    assert "total_medicoes_streaming" not in sinais["detalhes"]


@pytest.mark.skipif(not mimic.CHARTEVENTS.is_file(), reason="rode scripts/baixar_sinais.py")
def test_pipeline_com_dataset_real_faz_o_replay_do_mimic(tmp_path):
    config = ConfigPipeline.from_env(
        fator_velocidade=0.0, saida_dir=tmp_path, sinais_fonte="mimic"
    )
    resumo = OrquestradorPipeline(config).executar()
    relatorio = json.loads(resumo.caminho_relatorio.read_text(encoding="utf-8"))
    sinais = next(e for e in relatorio["etapas"] if e["etapa"] == "sinais_vitais")
    assert "MIMIC" in sinais["detalhes"]["fonte"]
    assert sinais["detalhes"]["total_medicoes_streaming"] > 0
