from azure.ai.textanalytics import TextAnalyticsClient
from azure.core.credentials import AzureKeyCredential

from app.config import (
    require_azure_language_endpoint,
    require_azure_language_key,
)


def get_language_client() -> TextAnalyticsClient:
    """Get Azure AI Language client for sentiment analysis"""
    endpoint = require_azure_language_endpoint()
    key = require_azure_language_key()
    return TextAnalyticsClient(endpoint=endpoint, credential=AzureKeyCredential(key))
