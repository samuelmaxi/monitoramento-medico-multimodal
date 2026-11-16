# Grupo de recursos Azure que agrupa todos os recursos do projeto
resource "azurerm_resource_group" "main" {
  name       = "${var.project_name}-${var.environment}-rg"
  location   = var.azure_region

  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# Storage Account com a cópia do JSON das transcrições (US11), enviada pela API e pelo pipeline
# (app/armazenamento_transcricoes.py). O áudio não é enviado.
resource "azurerm_storage_account" "media" {
  name                     = var.storage_account_name
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS"  # Local redundancy para reduzir custo
  https_traffic_only_enabled = true

  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# Container Blob das transcrições (JSON): api/<job>/... e pipeline/<execução>/...
resource "azurerm_storage_container" "audio" {
  name                  = var.container_name
  storage_account_name  = azurerm_storage_account.media.name
  container_access_type = "private"
}

# Retenção: as transcrições (fala do paciente) são apagadas blob_retention_days dias depois
# da última gravação.
resource "azurerm_storage_management_policy" "retencao" {
  storage_account_id = azurerm_storage_account.media.id

  rule {
    name    = "expira-transcricoes"
    enabled = true

    filters {
      blob_types   = ["blockBlob"]
      prefix_match = ["${var.container_name}/"]
    }

    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = var.blob_retention_days
      }
    }
  }
}


# Cognitive Services account para Speech-to-Text
resource "azurerm_cognitive_account" "speech" {
  name                = "${var.project_name}-speech-${var.environment}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  kind                = "SpeechServices"
  sku_name            = "F0"  # Free tier

  tags = {
    Project     = var.project_name
    Environment = var.environment
    Service     = "Speech"
  }
}

# Cognitive Services account para Language Analysis
resource "azurerm_cognitive_account" "language" {
  name                = "${var.project_name}-language-${var.environment}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  kind                = "TextAnalytics"
  sku_name            = "F0"  # Free tier

  tags = {
    Project     = var.project_name
    Environment = var.environment
    Service     = "Language"
  }
}
