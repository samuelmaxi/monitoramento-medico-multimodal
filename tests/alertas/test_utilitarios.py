"""Latência do alerta, configuração por ambiente e montagem do serviço."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from alertas import ConfigAlertas, cenario_news2_emergencia, construir
from alertas.armazenamento import ArmazenamentoJsonl
from alertas.latencia import latencias_segundos, ler_alertas, percentil

from .conftest import RAIZ, alerta_base


def _config(env: dict) -> ConfigAlertas:
    """Configuração de teste: regras apontando para o YAML do repositório, qualquer que seja o cwd."""
    return ConfigAlertas.from_env({"ALERTA_REGRAS_PATH": str(RAIZ / "config" / "alertas_regras.yaml"), **env})


def test_percentil_nearest_rank():
    valores = [float(v) for v in range(1, 101)]

    assert percentil(valores, 95) == 95.0
    assert percentil(valores, 100) == 100.0
    assert percentil([1.0, 2.0, 3.0, 4.0], 50) == 2.0


def test_percentil_sem_valores_falha():
    with pytest.raises(ValueError):
        percentil([], 95)


def test_latencia_usa_ingestao_e_cai_para_deteccao_quando_ausente(relogio):
    agora = relogio()
    com_ingestao = alerta_base(agora, ingested_at=agora - timedelta(seconds=4),
                               sent_at=agora + timedelta(seconds=1), latency_base="ingested_at")
    sem_ingestao = alerta_base(agora, sent_at=agora + timedelta(seconds=2), latency_base="detected_at")
    nao_enviado = alerta_base(agora, sent_at=None)

    assert latencias_segundos([com_ingestao, sem_ingestao, nao_enviado]) == [5.0, 2.0]


def test_ler_alertas_usa_o_ultimo_estado_de_cada_um(tmp_path, montar, relogio):
    store = ArmazenamentoJsonl(tmp_path)
    servico = montar(store, [])
    servico.motor.receber(cenario_news2_emergencia(relogio()))
    [alerta] = store.listar()
    caminho = tmp_path / "alertas.jsonl"
    versao_inicial = caminho.read_text(encoding="utf-8").splitlines()[0]
    versao_enviada = json.loads(versao_inicial)
    versao_enviada["sent_at"] = (relogio() + timedelta(seconds=3)).isoformat()
    caminho.write_text(versao_inicial + "\n" + json.dumps(versao_enviada) + "\n", encoding="utf-8")

    [lido] = ler_alertas(caminho)

    assert lido.id == alerta.id
    assert lido.sent_at is not None


def test_config_le_variaveis_e_separa_destinatarios():
    config = ConfigAlertas.from_env({
        "ALERTA_DESTINATARIOS": "medico@x, enfermagem@x,",
        "ALERTA_DESTINATARIOS_SIMULADO": "qa@x",
        "SMTP_HOST": "smtp.teste",
        "SMTP_REMETENTE": "alertas@teste",
        "SMTP_STARTTLS": "false",
        "ALERTA_PUBLIC_BASE_URL": "https://ex.test/",
        "ALERTA_TIMER_S": "15",
    })

    assert config.destinatarios == ("medico@x", "enfermagem@x")
    assert config.destinatarios_simulado == ("qa@x",)
    assert config.smtp_starttls is False
    assert config.base_url == "https://ex.test"
    assert config.timer_s == 15.0
    assert config.email_configurado


def test_config_vazia_nao_liga_email_nem_webhook():
    config = ConfigAlertas.from_env({})

    assert not config.email_configurado
    assert config.webhook_url is None
    assert config.armazenamento == "jsonl"


def test_construir_com_memoria_monta_o_servico():
    servico = construir(_config({"ALERTA_STORE": "memoria"}))

    assert servico.segredo_ack
    assert servico.armazenamento.listar() == []


def test_armazenamento_cosmos_ainda_nao_existe():
    with pytest.raises(ValueError, match="cosmos"):
        construir(_config({"ALERTA_STORE": "cosmos"}))


def test_regras_ausentes_falham_com_caminho_claro(tmp_path):
    config = ConfigAlertas.from_env({
        "ALERTA_STORE": "memoria",
        "ALERTA_REGRAS_PATH": str(tmp_path / "nao_existe.yaml"),
    })

    with pytest.raises(FileNotFoundError):
        construir(config)


def test_segredo_de_ack_vem_da_config_quando_definido():
    assert construir(_config({"ALERTA_STORE": "memoria", "ALERTA_ACK_SECRET": "meu-segredo"})).segredo_ack == "meu-segredo"
