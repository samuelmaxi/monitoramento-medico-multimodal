from azure.cognitiveservices.speech import SpeechConfig
from azure.storage.blob import BlobServiceClient
from azure.ai.textanalytics import TextAnalyticsClient
from azure.core.credentials import AzureKeyCredential

from app.config import (
    require_azure_speech_key,
    require_azure_speech_region,
    require_azure_storage_connection_string,
    require_azure_language_endpoint,
    require_azure_language_key,
)


def get_speech_config() -> SpeechConfig:
    """Get Azure Speech service config for speech-to-text"""
    speech_config = SpeechConfig(
        subscription=require_azure_speech_key(),
        region=require_azure_speech_region(),
    )
    speech_config.speech_recognition_language = "pt-BR"
    return speech_config


def get_blob_service_client() -> BlobServiceClient:
    """Get Azure Blob Storage client for audio file uploads"""
    return BlobServiceClient.from_connection_string(
        require_azure_storage_connection_string()
    )


def get_language_client() -> TextAnalyticsClient:
    """Get Azure AI Language client for sentiment analysis"""
    endpoint = require_azure_language_endpoint()
    key = require_azure_language_key()
    return TextAnalyticsClient(endpoint=endpoint, credential=AzureKeyCredential(key))
