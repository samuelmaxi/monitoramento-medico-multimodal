"""Cliente REST da API de transcrição rápida do Azure AI Speech (US11).

O áudio vai na própria requisição (sem Blob Storage) e a resposta é o JSON
bruto da API. Timeouts, erros de conexão e códigos 429/5xx são retentados com
espera exponencial (ou o valor de `Retry-After`). Outros 4xx falham de imediato.
"""

import json
import time
from pathlib import Path

import httpx

API_VERSION = "2025-10-15"
LOCALE = "pt-BR"
# Formatos aceitos pela transcrição rápida (WAV, MP3, OGG/Opus, FLAC, WMA, AAC, AMR, WebM, Speex).
FORMATOS_SUPORTADOS = frozenset({"wav", "mp3", "ogg", "opus", "flac", "wma", "aac", "amr", "webm", "speex"})
_STATUS_COM_RETRY = frozenset({429, 500, 502, 503, 504})
_ESPERA_MAXIMA_S = 60.0


class TranscricaoError(Exception):
    """Falha definitiva na transcrição. O arquivo de áudio nunca é alterado."""

    def __init__(self, mensagem: str, status_code: int | None = None, tentativas: int = 0):
        super().__init__(mensagem)
        self.status_code = status_code
        self.tentativas = tentativas


def transcrever_arquivo(
    audio_path: Path,
    *,
    endpoint: str,
    chave: str,
    frases: list[str] | None = None,
    max_speakers: int = 2,
    locale: str = LOCALE,
    timeout_s: float = 600.0,
    max_tentativas: int = 3,
    espera_base_s: float = 2.0,
    transporte: httpx.BaseTransport | None = None,
    dormir=time.sleep,
) -> dict:
    """Transcreve o arquivo em pt-BR com diarização e devolve o JSON da API.

    `frases` (phrase list) é enviada só quando informada. `transporte` e `dormir`
    existem para os testes.
    """
    definicao: dict = {
        "locales": [locale],
        "diarization": {"enabled": True, "maxSpeakers": max_speakers},
        "profanityFilterMode": "None",
    }
    if frases:
        definicao["phraseList"] = {"phrases": list(frases)}

    url = f"{endpoint.rstrip('/')}/speechtotext/transcriptions:transcribe"
    audio_bytes = audio_path.read_bytes()
    timeout = httpx.Timeout(timeout_s, connect=10.0)

    ultimo_status: int | None = None
    motivo = "sem resposta"
    with httpx.Client(timeout=timeout, transport=transporte) as cliente:
        for tentativa in range(1, max_tentativas + 1):
            espera = None
            try:
                resposta = cliente.post(
                    url,
                    headers={"Ocp-Apim-Subscription-Key": chave},
                    params={"api-version": API_VERSION},
                    data={"definition": json.dumps(definicao, ensure_ascii=False)},
                    files={"audio": (audio_path.name, audio_bytes, "application/octet-stream")},
                )
            except httpx.TransportError as exc:  # inclui TimeoutException
                motivo = f"{type(exc).__name__}: {exc}"
                ultimo_status = None
            else:
                if resposta.status_code == 200:
                    try:
                        return resposta.json()
                    except ValueError as exc:
                        raise TranscricaoError(
                            "Resposta do Azure Speech não é JSON válido.", 200, tentativa
                        ) from exc

                motivo = _descrever_erro(resposta)
                if resposta.status_code not in _STATUS_COM_RETRY:
                    raise TranscricaoError(motivo, resposta.status_code, tentativa)
                ultimo_status = resposta.status_code
                espera = _retry_after(resposta)

            if tentativa < max_tentativas:
                dormir(espera if espera is not None else espera_base_s * 2 ** (tentativa - 1))

    raise TranscricaoError(
        f"Falhou após {max_tentativas} tentativas: {motivo}", ultimo_status, max_tentativas
    )


def _descrever_erro(resposta: httpx.Response) -> str:
    try:
        mensagem = resposta.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        mensagem = resposta.text[:300]
    return f"HTTP {resposta.status_code}: {mensagem}"


def _retry_after(resposta: httpx.Response) -> float | None:
    valor = resposta.headers.get("Retry-After")
    if valor is None:
        return None
    try:
        return min(float(valor), _ESPERA_MAXIMA_S)
    except ValueError:
        return None
