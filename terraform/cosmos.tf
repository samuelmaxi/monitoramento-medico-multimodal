# Cosmos DB (NoSQL) para o motor de alertas (US19): estado dos alertas e trilha de auditoria.
# Free tier: 1000 RU/s e 25 GB sem custo. Só uma conta gratuita por assinatura.

resource "azurerm_cosmosdb_account" "alertas" {
  name                = "${var.project_name}-cosmos-${var.environment}"
  location            = var.cosmos_location
  resource_group_name = azurerm_resource_group.main.name
  offer_type          = "Standard"
  kind                = "GlobalDocumentDB"
  free_tier_enabled   = true

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = var.cosmos_location
    failover_priority = 0
  }

  tags = {
    Project     = var.project_name
    Environment = var.environment
    Service     = "Alertas"
    ManagedBy   = "terraform"
  }
}

# Banco único; o throughput fica no banco e é dividido pelos containers.
resource "azurerm_cosmosdb_sql_database" "alertas" {
  name                = "alertas"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.alertas.name
  throughput          = var.cosmos_throughput
}

# Estado atual de cada alerta. Partição por paciente: consulta por paciente é barata.
resource "azurerm_cosmosdb_sql_container" "alerts" {
  name                  = "alerts"
  resource_group_name   = azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.alertas.name
  database_name         = azurerm_cosmosdb_sql_database.alertas.name
  partition_key_paths   = ["/patient_id"]
  partition_key_version = 2
}

# Trilha de auditoria, append-only: criado, suprimido, escalonado, enviado, falhou, reconhecido.
resource "azurerm_cosmosdb_sql_container" "alert_events" {
  name                  = "alert_events"
  resource_group_name   = azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.alertas.name
  database_name         = azurerm_cosmosdb_sql_database.alertas.name
  partition_key_paths   = ["/patient_id"]
  partition_key_version = 2
}
