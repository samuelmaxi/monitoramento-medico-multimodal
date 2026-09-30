import boto3
from botocore.config import Config

from app.config import require_aws_region

# Retry mode "adaptive" já faz backoff exponencial com jitter para throttling
# (HTTP 429 / ThrottlingException) antes da exceção chegar ao nosso código.
_RETRY_CONFIG = Config(retries={"max_attempts": 5, "mode": "adaptive"})


def get_transcribe_client():
    return boto3.client(
        "transcribe", region_name=require_aws_region(), config=_RETRY_CONFIG
    )


def get_comprehend_client():
    return boto3.client(
        "comprehend", region_name=require_aws_region(), config=_RETRY_CONFIG
    )


def get_s3_client():
    return boto3.client("s3", region_name=require_aws_region(), config=_RETRY_CONFIG)
