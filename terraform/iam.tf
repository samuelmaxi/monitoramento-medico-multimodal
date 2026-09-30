# IAM user dedicado que os scripts Python usarão para chamar Transcribe/Comprehend/S3.
# A access key NÃO é criada aqui de propósito: gerar a key via Terraform gravaria a
# secret no state. A key deve ser criada manualmente após o apply:
#   aws iam create-access-key --user-name <iam_user_name>
resource "aws_iam_user" "app" {
  name = var.iam_user_name
}

data "aws_iam_policy_document" "app_permissions" {
  # Amazon Transcribe não suporta permissões em nível de recurso para transcription
  # jobs — a API exige Resource = "*". O least privilege real vem da lista fechada
  # de actions (nunca "transcribe:*") e da condição de região abaixo.
  statement {
    sid = "TranscribeJobs"
    actions = [
      "transcribe:StartTranscriptionJob",
      "transcribe:GetTranscriptionJob",
      "transcribe:ListTranscriptionJobs",
      "transcribe:DeleteTranscriptionJob",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  # Amazon Comprehend DetectSentiment/BatchDetectSentiment também não suportam
  # permissões em nível de recurso — mesma limitação de API do Transcribe.
  statement {
    sid = "ComprehendSentiment"
    actions = [
      "comprehend:DetectSentiment",
      "comprehend:BatchDetectSentiment",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  statement {
    sid = "AudioTranscriptsBucketAccess"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
    ]
    resources = [
      aws_s3_bucket.media.arn,
      "${aws_s3_bucket.media.arn}/*",
    ]
  }
}

resource "aws_iam_policy" "app_permissions" {
  name   = "${var.project_name}-app-permissions"
  policy = data.aws_iam_policy_document.app_permissions.json
}

resource "aws_iam_user_policy_attachment" "app" {
  user       = aws_iam_user.app.name
  policy_arn = aws_iam_policy.app_permissions.arn
}
