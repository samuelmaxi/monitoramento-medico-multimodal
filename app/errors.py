from azure.core.exceptions import AzureError, ClientAuthenticationError
from fastapi import HTTPException


def translate_client_error(exc: AzureError) -> HTTPException:
    """Translate Azure service errors to HTTP exceptions"""
    error_code = getattr(exc, "error_code", None) or "UnknownError"
    message = str(exc)

    if isinstance(exc, ClientAuthenticationError):
        return HTTPException(
            status_code=401,
            detail=(
                "Credenciais Azure inválidas ou expiradas. Verifique "
                "AZURE_SPEECH_KEY, AZURE_LANGUAGE_KEY e AZURE_STORAGE_CONNECTION_STRING no .env."
            ),
        )

    if "ThrottlingException" in error_code or "429" in message or "rate" in message.lower():
        return HTTPException(
            status_code=429,
            detail=(
                "Azure está limitando as requisições (throttling); "
                "tente novamente em alguns instantes."
            ),
        )

    if "Forbidden" in error_code or "403" in message:
        return HTTPException(
            status_code=403,
            detail=(
                "Credenciais Azure válidas, mas sem permissão para esta ação. "
                "Verifique as permissões no Azure IAM/RBAC."
            ),
        )

    return HTTPException(status_code=502, detail=f"Erro do Azure ({error_code}): {message}")
