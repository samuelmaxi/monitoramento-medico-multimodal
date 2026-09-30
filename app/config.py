import os

AWS_REGION = os.getenv("AWS_REGION")
AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME")


def require_aws_region() -> str:
    if not AWS_REGION:
        raise RuntimeError("A variável AWS_REGION não foi definida no arquivo .env.")
    return AWS_REGION


def require_s3_bucket_name() -> str:
    if not AWS_S3_BUCKET_NAME:
        raise RuntimeError(
            "A variável AWS_S3_BUCKET_NAME não foi definida no arquivo .env."
        )
    return AWS_S3_BUCKET_NAME
