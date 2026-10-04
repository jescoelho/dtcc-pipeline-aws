# Observabilidade do Glue Bronze desta fonte -- ver docs/DECISOES.md
# ("Observabilidade, etapa 1" e "Boas práticas, etapa 4") pelo
# raciocínio completo. O tópico SNS é compartilhado entre fontes
# (var.sns_topic_arn, ver ../main.tf) -- um só canal de alerta, não
# importa qual fonte ou qual causa.

resource "aws_cloudwatch_event_rule" "glue_bronze_failed" {
  name        = "${local.nome}-glue-bronze-failed"
  description = "Job Glue Bronze desta fonte terminou em FAILED, TIMEOUT ou ERROR"

  event_pattern = jsonencode({
    source        = ["aws.glue"]
    "detail-type" = ["Glue Job State Change"]
    detail = {
      jobName = [aws_glue_job.bronze.name]
      state   = ["FAILED", "TIMEOUT", "ERROR"]
    }
  })
}

resource "aws_cloudwatch_event_target" "glue_bronze_failed_to_sns" {
  rule      = aws_cloudwatch_event_rule.glue_bronze_failed.name
  target_id = "sns-alertas"
  arn       = var.sns_topic_arn
}

# ---------- Desfecho do Glue na tabela de controle (toda execução) ----------

data "archive_file" "job_concluido_lambda" {
  type        = "zip"
  source_file = "${path.module}/../../../lambda/job_concluido.py"
  output_path = "${path.module}/../../.build/${var.fonte.nome}_job_concluido.zip"
}

resource "aws_iam_role" "job_concluido_lambda" {
  name               = "${local.nome}-job-concluido-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "job_concluido_lambda_logs" {
  role       = aws_iam_role.job_concluido_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "job_concluido_permissions" {
  statement {
    actions   = ["glue:GetJobRun"]
    resources = [aws_glue_job.bronze.arn]
  }
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${var.bucket_arn}/logs/execucoes/*"]
  }
}

resource "aws_iam_role_policy" "job_concluido_permissions" {
  role   = aws_iam_role.job_concluido_lambda.id
  policy = data.aws_iam_policy_document.job_concluido_permissions.json
}

resource "aws_lambda_function" "job_concluido" {
  function_name    = "${local.nome}-job-concluido"
  role             = aws_iam_role.job_concluido_lambda.arn
  handler          = "job_concluido.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 128
  filename         = data.archive_file.job_concluido_lambda.output_path
  source_code_hash = data.archive_file.job_concluido_lambda.output_base64sha256

  environment {
    variables = {
      BUCKET = var.bucket_name
    }
  }
}

resource "aws_cloudwatch_event_rule" "glue_bronze_concluido" {
  name        = "${local.nome}-glue-bronze-concluido"
  description = "Job Glue Bronze desta fonte terminou, qualquer desfecho -- grava na tabela de controle"

  event_pattern = jsonencode({
    source        = ["aws.glue"]
    "detail-type" = ["Glue Job State Change"]
    detail = {
      jobName = [aws_glue_job.bronze.name]
      state   = ["SUCCEEDED", "FAILED", "TIMEOUT", "ERROR", "STOPPED"]
    }
  })
}

resource "aws_cloudwatch_event_target" "glue_bronze_concluido_to_lambda" {
  rule      = aws_cloudwatch_event_rule.glue_bronze_concluido.name
  target_id = "job-concluido"
  arn       = aws_lambda_function.job_concluido.arn

  dead_letter_config {
    arn = var.dlq_arn
  }

  retry_policy {
    maximum_retry_attempts       = 3
    maximum_event_age_in_seconds = 3600
  }
}

resource "aws_lambda_permission" "allow_eventbridge_job_concluido" {
  statement_id  = "AllowEventBridgeInvokeJobConcluido"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.job_concluido.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.glue_bronze_concluido.arn
}

output "lambda_job_concluido" {
  value = aws_lambda_function.job_concluido.function_name
}
output "glue_bronze_concluido_rule_arn" {
  value = aws_cloudwatch_event_rule.glue_bronze_concluido.arn
}
