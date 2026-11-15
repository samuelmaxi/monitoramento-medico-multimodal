"""Erros de ingestão. Cada um carrega um ``codigo`` estável para logs, metadados e API."""

from __future__ import annotations


class ErroDeAudio(ValueError):
    """Áudio que não pode ser ingerido. O lote registra o erro e segue para o próximo arquivo."""

    codigo = "audio_invalido"


class ArquivoNaoEncontrado(ErroDeAudio):
    codigo = "arquivo_nao_encontrado"


class AudioVazio(ErroDeAudio):
    """Arquivo de 0 bytes, sem amostras ou só silêncio digital."""

    codigo = "audio_vazio"


class AudioCorrompido(ErroDeAudio):
    """O arquivo existe, mas não pôde ser decodificado (ou tem amostras inválidas)."""

    codigo = "audio_corrompido"


class FormatoNaoSuportado(ErroDeAudio):
    codigo = "formato_nao_suportado"


class AudioCurto(ErroDeAudio):
    codigo = "audio_curto"


class AudioLongo(ErroDeAudio):
    codigo = "audio_longo"


class AudioIdInvalido(ErroDeAudio):
    """``audio_id`` que não serve de nome de pasta (ex.: ``..``): sairia da pasta de saída."""

    codigo = "audio_id_invalido"
