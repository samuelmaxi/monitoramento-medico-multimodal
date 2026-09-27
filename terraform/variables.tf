variable "aws_region" {
  description = "Região AWS onde os serviços de Transcribe, Comprehend e S3 serão utilizados. Fixada em us-east-1 pela maior cobertura de features e menor custo."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Nome do projeto, usado como prefixo para nomear os recursos AWS."
  type        = string
  default     = "monitoramento-medico-multimodal"
}

variable "environment" {
  description = "Ambiente da infraestrutura (ex.: dev, staging, prod). Usado apenas para tagging."
  type        = string
  default     = "dev"
}

variable "iam_user_name" {
  description = "Nome do IAM user dedicado que os scripts Python usarão para chamar Transcribe/Comprehend/S3."
  type        = string
  default     = "monitoramento-medico-app"
}

variable "enable_bucket_versioning" {
  description = "Habilita versionamento no bucket S3 de mídia/transcrições. Desabilitado por padrão para evitar acúmulo de custo com versões antigas."
  type        = bool
  default     = false
}

variable "object_expiration_days" {
  description = "Número de dias até os objetos do bucket S3 expirarem automaticamente (controle de custo de storage)."
  type        = number
  default     = 30
}

variable "budget_limit_usd" {
  description = "Teto mensal de gasto (em USD) para o AWS Budget de alerta de custo."
  type        = number
  default     = 5
}

variable "budget_alert_email" {
  description = "E-mail que receberá os alertas do AWS Budget quando o gasto real ou previsto ultrapassar os limites configurados."
  type        = string
}
