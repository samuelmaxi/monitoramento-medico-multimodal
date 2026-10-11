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

# Storage Account para armazenar arquivos de áudio
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

# Container Blob para arquivos de áudio
resource "azurerm_storage_container" "audio" {
  name                  = var.container_name
  storage_account_name  = azurerm_storage_account.media.name
  container_access_type = "private"
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
