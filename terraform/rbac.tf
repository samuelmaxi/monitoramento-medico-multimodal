# RBAC (Role-Based Access Control) assignments para Azure
# Define quem tem permissão para acessar os recursos

# Role: Storage Blob Data Contributor - permite ler/escrever/deletar blobs
# Atribuído ao usuário/app que está rodando a aplicação
resource "azurerm_role_assignment" "storage_blob_contributor" {
  scope              = azurerm_storage_account.media.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id       = data.azurerm_client_config.current.object_id
}

# Role: Cognitive Services User - permite usar Speech e Language services
resource "azurerm_role_assignment" "speech_user" {
  scope              = azurerm_cognitive_account.speech.id
  role_definition_name = "Cognitive Services User"
  principal_id       = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "language_user" {
  scope              = azurerm_cognitive_account.language.id
  role_definition_name = "Cognitive Services User"
  principal_id       = data.azurerm_client_config.current.object_id
}

# Alternativa para CI/CD: descomente e configure um Service Principal
# resource "azurerm_service_principal" "app" {
#   client_id = var.app_client_id  # Passar como variável
#
#   depends_on = [
#     azurerm_role_assignment.storage_blob_contributor,
#     azurerm_role_assignment.speech_user,
#     azurerm_role_assignment.language_user
#   ]
# }
