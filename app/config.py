import os

AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION")
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
AZURE_LANGUAGE_ENDPOINT = os.getenv("AZURE_LANGUAGE_ENDPOINT")
AZURE_LANGUAGE_KEY = os.getenv("AZURE_LANGUAGE_KEY")
AZURE_BLOB_CONTAINER_NAME = os.getenv("AZURE_BLOB_CONTAINER_NAME", "audio-transcripts")


def require_azure_speech_key() -> str:
    if not AZURE_SPEECH_KEY:
        raise RuntimeError("A variável AZURE_SPEECH_KEY não foi definida no arquivo .env.")
    return AZURE_SPEECH_KEY


def require_azure_speech_region() -> str:
    if not AZURE_SPEECH_REGION:
        raise RuntimeError("A variável AZURE_SPEECH_REGION não foi definida no arquivo .env.")
    return AZURE_SPEECH_REGION


def require_azure_storage_connection_string() -> str:
    if not AZURE_STORAGE_CONNECTION_STRING:
        raise RuntimeError(
            "A variável AZURE_STORAGE_CONNECTION_STRING não foi definida no arquivo .env."
        )
    return AZURE_STORAGE_CONNECTION_STRING


def require_azure_language_endpoint() -> str:
    if not AZURE_LANGUAGE_ENDPOINT:
        raise RuntimeError("A variável AZURE_LANGUAGE_ENDPOINT não foi definida no arquivo .env.")
    return AZURE_LANGUAGE_ENDPOINT


def require_azure_language_key() -> str:
    if not AZURE_LANGUAGE_KEY:
        raise RuntimeError("A variável AZURE_LANGUAGE_KEY não foi definida no arquivo .env.")
    return AZURE_LANGUAGE_KEY


def require_blob_container_name() -> str:
    return AZURE_BLOB_CONTAINER_NAME
