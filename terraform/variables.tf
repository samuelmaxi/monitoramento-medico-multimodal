variable "azure_region" {
  description = "Região Azure onde os serviços de Speech, Language e Storage serão criados. Usar regiões com tier gratuita (ex.: eastus)."
  type        = string
  default     = "eastus"
}

variable "project_name" {
  description = "Nome do projeto, usado como prefixo para nomear os recursos Azure."
  type        = string
  default     = "monitoramento-medico-multimodal"
}

variable "environment" {
  description = "Ambiente da infraestrutura (ex.: dev, staging, prod). Usado para tagging e naming."
  type        = string
  default     = "dev"
}

variable "storage_account_name" {
  description = "Nome único da conta de storage Azure (máximo 24 caracteres, apenas letras e números)."
  type        = string
  default     = "medicalmonitoring"
}

variable "container_name" {
  description = "Nome do container Blob Storage para arquivos de áudio."
  type        = string
  default     = "audio-transcripts"
}

variable "blob_retention_days" {
  description = "Número de dias para retenção de blobs no storage (controle de custo)."
  type        = number
  default     = 30
}

variable "alert_email" {
  description = "E-mail para receber alertas de consumo de serviços Azure."
  type        = string
}

variable "subscription_id" {
  description = "ID da assinatura Azure onde os recursos serão provisionados."
  type        = string
  default     = "14f9a7ae-f7b8-4f3c-8cb9-6ddc80cc1f22"
}