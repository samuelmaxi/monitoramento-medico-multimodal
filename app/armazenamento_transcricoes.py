"""Cópia do JSON das transcrições (US11) no Azure Blob Storage.

A storage account e o container vêm do Terraform (``terraform/resources.tf``); a conexão vem de
``AZURE_STORAGE_CONNECTION_STRING`` e o container de ``AZURE_BLOB_CONTAINER_NAME``. Sem a connection
string o envio fica desligado. Só o JSON vai para a nuvem, nunca o áudio, e o envio é uma cópia: a
transcrição local continua sendo a de referência, e uma falha no envio só gera um aviso.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

log = logging.getLogger("app.armazenamento_transcricoes")

CONTAINER_PADRAO = "audio-transcripts"
CONTENT_TYPE_JSON = "application/json; charset=utf-8"
# Storage fora do ar não pode segurar a requisição da API por minutos.
OPCOES_CLIENTE = {"connection_timeout": 10, "read_timeout": 30, "retry_total": 2}


class ArquivadorBlob:
    """Envia arquivos para um container do Blob Storage."""

    def __init__(self, container: Any) -> None:
        self.container = container  # ContainerClient (ou um falso nos testes)

    @property
    def nome_container(self) -> str:
        return str(getattr(self.container, "container_name", "?"))

    @classmethod
    def do_ambiente(cls, ambiente: Mapping[str, str] | None = None) -> ArquivadorBlob | None:
        """Arquivador do ``.env``; ``None`` sem ``AZURE_STORAGE_CONNECTION_STRING``.

        Lê o ambiente na hora da chamada, não no import, para os testes poderem desligar o envio.
        """
        ambiente = os.environ if ambiente is None else ambiente
        conexao = ambiente.get("AZURE_STORAGE_CONNECTION_STRING")
        if not conexao:
            return None
        from azure.storage.blob import BlobServiceClient

        nome = ambiente.get("AZURE_BLOB_CONTAINER_NAME") or CONTAINER_PADRAO
        servico = BlobServiceClient.from_connection_string(conexao, **OPCOES_CLIENTE)
        return cls(servico.get_container_client(nome))

    def enviar(self, arquivo: Path, nome_blob: str) -> None:
        from azure.storage.blob import ContentSettings

        self.container.upload_blob(
            nome_blob,
            arquivo.read_bytes(),
            overwrite=True,
            content_settings=ContentSettings(content_type=CONTENT_TYPE_JSON),
        )


def arquivar_transcricao(
    caminho_json: Path, nome_blob: str, *, arquivador: ArquivadorBlob | None = None
) -> str:
    """Envia o JSON da transcrição e devolve a situação, legível. Nunca levanta exceção.

    ``nome_blob`` deve ser feito de IDs gerados ou fixos, nunca do nome do arquivo enviado (que
    pode trazer o nome do paciente).
    """
    try:
        arquivador = arquivador or ArquivadorBlob.do_ambiente()
        if arquivador is None:
            return "desligado: AZURE_STORAGE_CONNECTION_STRING vazia"
        arquivador.enviar(caminho_json, nome_blob)
    except Exception as exc:
        # A mensagem do SDK não traz a connection string, só conta e container.
        log.warning("cópia da transcrição no Blob Storage falhou (%s): %s", nome_blob, exc)
        return f"falhou: {type(exc).__name__}: {exc}"
    return f"enviado: {arquivador.nome_container}/{nome_blob}"
