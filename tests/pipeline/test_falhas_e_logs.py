"""Falha por etapa e log estruturado do pipeline (US20, DoD 4)."""

import json
import logging

import pytest

from pipeline import ConfigPipeline, OrquestradorPipeline
from pipeline.etapas.sinais import EtapaSinaisVitais
from pipeline.logger import LoggerPipeline, configurar_logging


@pytest.fixture(autouse=True)
def _chave(monkeypatch):
    monkeypatch.setenv("PSEUDONYM_KEY", "chave-de-teste-com-mais-de-32-bytes-000000")


def _quebrar_sinais(monkeypatch):
    def quebrado(self, contexto):
        contexto.logger.iniciar_etapa(self.nome)
        raise RuntimeError("sensor offline")

    monkeypatch.setattr(EtapaSinaisVitais, "executar", quebrado)


def _config(tmp_path, **extra):
    return ConfigPipeline.from_env(
        fator_velocidade=0.0, saida_dir=tmp_path, sinais_fonte="sintetico", **extra
    )


def test_etapa_com_falha_e_registrada_e_o_fluxo_segue(tmp_path, monkeypatch):
    _quebrar_sinais(monkeypatch)
    resumo = OrquestradorPipeline(_config(tmp_path)).executar()
    relatorio = json.loads(resumo.caminho_relatorio.read_text(encoding="utf-8"))
    status = {e["etapa"]: e["status"] for e in relatorio["etapas"]}

    assert status["sinais_vitais"] == "falha"
    assert all(v == "sucesso" for k, v in status.items() if k != "sinais_vitais")
    assert list(status) == [
        "video", "audio", "sinais_vitais", "prescricoes", "movimentacao", "fusao", "alertas"
    ]
    assert resumo.etapas_com_falha == ("sinais_vitais",)
    assert relatorio["etapas_com_falha"] == ["sinais_vitais"]
    falha = next(e for e in relatorio["etapas"] if e["etapa"] == "sinais_vitais")
    assert "RuntimeError: sensor offline" in falha["detalhes"]["erro"]


def test_abortar_na_falha_interrompe_as_etapas_seguintes(tmp_path, monkeypatch):
    _quebrar_sinais(monkeypatch)
    resumo = OrquestradorPipeline(_config(tmp_path, abortar_na_falha=True)).executar()
    relatorio = json.loads(resumo.caminho_relatorio.read_text(encoding="utf-8"))
    assert [e["etapa"] for e in relatorio["etapas"]] == ["video", "audio", "sinais_vitais"]


def test_log_jsonl_tem_uma_linha_json_por_inicio_e_fim_de_etapa(tmp_path):
    resumo = OrquestradorPipeline(_config(tmp_path)).executar()
    linhas = [json.loads(x) for x in resumo.caminho_log.read_text(encoding="utf-8").splitlines()]
    assert [x["evento"] for x in linhas] == ["etapa_iniciada", "etapa_concluida"] * 7
    concluidas = [x for x in linhas if x["evento"] == "etapa_concluida"]
    assert all(x["duracao_ms"] >= 0 and x["status"] == "sucesso" for x in concluidas)


def test_etapa_que_cai_antes_de_iniciar_tambem_e_registrada(tmp_path):
    logger = LoggerPipeline(tmp_path)
    logger.falhar_etapa("video", ValueError("x"))
    assert logger.houve_falha
    assert logger.registros[0].etapa == "video"


def test_configurar_logging_envia_o_json_das_etapas_ao_stderr(tmp_path, capsys):
    configurar_logging(logging.INFO)
    try:
        logger = LoggerPipeline(tmp_path)
        logger.iniciar_etapa("audio")
        logger.concluir_etapa(eventos_emitidos=2)
    finally:
        logging.getLogger("pipeline").handlers = []
    linhas = [json.loads(x) for x in capsys.readouterr().err.splitlines() if x.startswith("{")]
    assert [x["evento"] for x in linhas] == ["etapa_iniciada", "etapa_concluida"]
    assert linhas[1]["etapa"] == "audio" and linhas[1]["eventos_emitidos"] == 2


def test_log_jsonl_e_reescrito_a_cada_execucao_mas_sobrevive_a_um_novo_logger(tmp_path):
    OrquestradorPipeline(_config(tmp_path)).executar()
    resumo = OrquestradorPipeline(_config(tmp_path)).executar()
    assert len(resumo.caminho_log.read_text(encoding="utf-8").splitlines()) == 14
    LoggerPipeline(tmp_path)  # o demo recria o logger só para exibir a tabela
    assert resumo.caminho_log.is_file()
