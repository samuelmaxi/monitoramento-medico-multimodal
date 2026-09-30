output "s3_bucket_name" {
  description = "Nome do bucket S3 usado para áudio de entrada e transcripts do Amazon Transcribe."
  value       = aws_s3_bucket.media.bucket
}

output "s3_bucket_arn" {
  description = "ARN do bucket S3 usado para áudio de entrada e transcripts do Amazon Transcribe."
  value       = aws_s3_bucket.media.arn
}

output "iam_user_name" {
  description = "Nome do IAM user dedicado para os scripts Python. Use este nome para gerar a access key manualmente."
  value       = aws_iam_user.app.name
}

output "iam_user_arn" {
  description = "ARN do IAM user dedicado para os scripts Python."
  value       = aws_iam_user.app.arn
}

output "iam_policy_arn" {
  description = "ARN da IAM policy com as permissões mínimas de Transcribe/Comprehend/S3."
  value       = aws_iam_policy.app_permissions.arn
}
