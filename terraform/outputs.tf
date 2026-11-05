output "storage_account_name" {
  description = "Nome da conta de storage Azure para armazenar áudio e transcripts."
  value       = azurerm_storage_account.media.name
}

output "storage_account_id" {
  description = "ID da conta de storage Azure."
  value       = azurerm_storage_account.media.id
}

output "storage_connection_string" {
  description = "Connection string da conta de storage (usar em AZURE_STORAGE_CONNECTION_STRING no .env)."
  value       = azurerm_storage_account.media.primary_blob_connection_string
  sensitive   = true
}

output "speech_key" {
  description = "API key do serviço Speech (usar em AZURE_SPEECH_KEY no .env)."
  value       = azurerm_cognitive_account.speech.primary_access_key
  sensitive   = true
}

output "speech_endpoint" {
  description = "Endpoint do serviço Speech."
  value       = azurerm_cognitive_account.speech.endpoint
}

output "language_key" {
  description = "API key do serviço Language (usar em AZURE_LANGUAGE_KEY no .env)."
  value       = azurerm_cognitive_account.language.primary_access_key
  sensitive   = true
}

output "language_endpoint" {
  description = "Endpoint do serviço Language (usar em AZURE_LANGUAGE_ENDPOINT no .env)."
  value       = azurerm_cognitive_account.language.endpoint
}

output "resource_group_name" {
  description = "Nome do grupo de recursos Azure."
  value       = azurerm_resource_group.main.name
}

output "container_name" {
  description = "Nome do container Blob Storage para arquivos de áudio."
  value       = azurerm_storage_container.audio.name
}

output "cosmos_endpoint" {
  description = "Endpoint do Cosmos DB de alertas (usar em COSMOS_ENDPOINT no .env)."
  value       = azurerm_cosmosdb_account.alertas.endpoint
}

output "cosmos_key" {
  description = "Chave primária do Cosmos DB de alertas (usar em COSMOS_KEY no .env)."
  value       = azurerm_cosmosdb_account.alertas.primary_key
  sensitive   = true
}

output "cosmos_database" {
  description = "Nome do banco no Cosmos DB de alertas (usar em COSMOS_DATABASE no .env)."
  value       = azurerm_cosmosdb_sql_database.alertas.name
}
