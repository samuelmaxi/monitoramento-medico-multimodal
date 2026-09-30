from botocore.exceptions import ClientError
from fastapi import HTTPException

_THROTTLING_CODES = {"ThrottlingException", "TooManyRequestsException"}
_UNAUTHORIZED_CODES = {"UnrecognizedClientException", "InvalidSignatureException"}
_FORBIDDEN_CODES = {"AccessDeniedException"}


def translate_client_error(exc: ClientError) -> HTTPException:
    error = exc.response.get("Error", {})
    code = error.get("Code", "UnknownError")
    message = error.get("Message", str(exc))

    if code in _THROTTLING_CODES:
        return HTTPException(
            status_code=429,
            detail=(
                "AWS está limitando as requisições (throttling); "
                "tente novamente em alguns instantes."
            ),
        )
    if code in _UNAUTHORIZED_CODES:
        return HTTPException(
            status_code=401,
            detail=(
                "Credenciais AWS inválidas ou expiradas. Verifique "
                "AWS_ACCESS_KEY_ID e AWS_SECRET_ACCESS_KEY no .env."
            ),
        )
    if code in _FORBIDDEN_CODES:
        return HTTPException(
            status_code=403,
            detail=(
                "Credenciais AWS válidas, mas sem permissão para esta ação. "
                "Verifique a IAM policy anexada em terraform/iam.tf."
            ),
        )
    return HTTPException(status_code=502, detail=f"Erro da AWS ({code}): {message}")
