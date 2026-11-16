"""Blob Storage falso para os testes da cópia das transcrições (sem rede)."""


class ContainerFalso:
    """Faz o papel do ``ContainerClient`` do Azure: guarda o que seria enviado."""

    container_name = "audio-transcripts"

    def __init__(self, erro: Exception | None = None) -> None:
        self.erro = erro
        self.envios: list[dict] = []

    def upload_blob(self, nome, dados, **opcoes):
        if self.erro is not None:
            raise self.erro
        self.envios.append({"nome": nome, "dados": dados, **opcoes})
