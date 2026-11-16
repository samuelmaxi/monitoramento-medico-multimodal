import logging

from app.armazenamento_transcricoes import (
    CONTENT_TYPE_JSON,
    ArquivadorBlob,
    arquivar_transcricao,
)
from tests.blob_falso import ContainerFalso


def _json(tmp_path):
    caminho = tmp_path / "audio.json"
    caminho.write_text('{"texto_completo": "falta de ar"}', encoding="utf-8")
    return caminho


def test_sem_connection_string_o_envio_fica_desligado(tmp_path):
    assert ArquivadorBlob.do_ambiente({}) is None
    situacao = arquivar_transcricao(_json(tmp_path), "api/job/audio.json")
    assert situacao.startswith("desligado")


def test_do_ambiente_usa_o_container_configurado_ou_o_padrao():
    conexao = (
        "DefaultEndpointsProtocol=https;AccountName=contafalsa;AccountKey=YWJj;"
        "EndpointSuffix=core.windows.net"
    )
    padrao = ArquivadorBlob.do_ambiente({"AZURE_STORAGE_CONNECTION_STRING": conexao})
    outro = ArquivadorBlob.do_ambiente(
        {"AZURE_STORAGE_CONNECTION_STRING": conexao, "AZURE_BLOB_CONTAINER_NAME": "outro"}
    )
    assert padrao.nome_container == "audio-transcripts"
    assert outro.nome_container == "outro"


def test_envia_o_json_com_nome_conteudo_e_tipo(tmp_path):
    container = ContainerFalso()
    caminho = _json(tmp_path)

    situacao = arquivar_transcricao(
        caminho, "api/job-1/audio.json", arquivador=ArquivadorBlob(container)
    )

    assert situacao == "enviado: audio-transcripts/api/job-1/audio.json"
    (envio,) = container.envios
    assert envio["nome"] == "api/job-1/audio.json"
    assert envio["dados"] == caminho.read_bytes()
    assert envio["overwrite"] is True
    assert envio["content_settings"].content_type == CONTENT_TYPE_JSON


def test_falha_no_envio_vira_aviso_sem_excecao(tmp_path, caplog):
    container = ContainerFalso(erro=ConnectionError("storage fora do ar"))

    with caplog.at_level(logging.WARNING, logger="app.armazenamento_transcricoes"):
        situacao = arquivar_transcricao(
            _json(tmp_path), "api/job/audio.json", arquivador=ArquivadorBlob(container)
        )

    assert situacao == "falhou: ConnectionError: storage fora do ar"
    assert "storage fora do ar" in caplog.text


def test_connection_string_malformada_vira_aviso_sem_excecao(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", "segredo-malformado")

    situacao = arquivar_transcricao(_json(tmp_path), "api/job/audio.json")

    assert situacao.startswith("falhou: ValueError")
    assert "segredo-malformado" not in situacao
