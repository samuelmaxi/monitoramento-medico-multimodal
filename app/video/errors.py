class VideoError(Exception):
    """Erro base do pipeline de vídeo."""


class InvalidVideoFormatError(VideoError):
    """Extensão não suportada ou metadados inválidos (ex.: fps zero)."""


class CorruptedVideoError(VideoError):
    """Arquivo que não pode ser aberto ou decodificado."""
