# Observabilidade, etapa 1: avisar quando o job Glue Bronze falhar.
#
# Caminho 100% nativo da AWS, sem Lambda nova: o próprio Glue emite um
# evento de mudança de estado no EventBridge; uma regra filtra esse evento
# para FAILED/TIMEOUT/ERROR e publica num tópico SNS, que manda e-mail.
# Reaproveita var.budget_email (já usado pelo alerta de custo) -- não é
# o mesmo mecanismo (budget é custo, isto é execução), mas é o mesmo
# destinatário: você.
#
# Escopo desta etapa: só falha. "Sucesso" não é incluído de propósito --
# um e-mail por execução bem-sucedida, todo dia, é ruído que ensina a
# ignorar o canal. Falha é o caso em que alguém realmente precisa olhar.
#
# A checagem de qualidade de dados ANTES do Glue rodar (ver README,
# "Tarefas futuras") continua pendente -- isto aqui cobre só "o job
# rodou e explodiu", não "o dado que chegou está estranho".

resource "aws_sns_topic" "alertas" {
  name = "${var.prefix}-alertas"
}

data "aws_iam_policy_document" "alertas_topic_policy" {
  statement {
    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.alertas.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_sns_topic_policy" "alertas" {
  arn    = aws_sns_topic.alertas.arn
  policy = data.aws_iam_policy_document.alertas_topic_policy.json
}

resource "aws_sns_topic_subscription" "alertas_email" {
  topic_arn = aws_sns_topic.alertas.arn
  protocol  = "email"
  endpoint  = var.budget_email
}

resource "aws_cloudwatch_event_rule" "glue_bronze_failed" {
  name        = "${var.prefix}-glue-bronze-failed"
  description = "Job Glue Bronze terminou em FAILED, TIMEOUT ou ERROR"

  event_pattern = jsonencode({
    source      = ["aws.glue"]
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
  arn       = aws_sns_topic.alertas.arn
}

output "sns_alertas" {
  value = aws_sns_topic.alertas.arn
}

# ---------- Desfecho do Glue na tabela de controle (toda execução, não só falha) ----------
#
# Achado da avaliação end-to-end (ver README): a tabela de controle só
# tinha o "disparado" do trigger_bronze -- nunca o desfecho. Regra
# separada da glue_bronze_failed de propósito: aquela é só pra
# FAILED/TIMEOUT/ERROR e manda e-mail (ruído seria mandar e-mail a cada
# sucesso); esta cobre TODO término, inclusive SUCCEEDED, e só grava na
# tabela -- sem e-mail. Mesmo evento nativo do Glue, dois consumidores
# independentes, mesmo padrão já usado para o CSV chegando em raw/dtcc/.

data "archive_file" "job_concluido_lambda" {
  type        = "zip"
  source_file = "${path.module}/../lambda/job_concluido.py"
  output_path = "${path.module}/.build/job_concluido.zip"
}

resource "aws_iam_role" "job_concluido_lambda" {
  name               = "${var.prefix}-job-concluido-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "job_concluido_lambda_logs" {
  role       = aws_iam_role.job_concluido_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "job_concluido_permissions" {
  statement {
    # Busca o run completo -- o evento do Glue não traz os argumentos
    # (execution_id) nem a duração, só jobName/jobRunId/state.
    actions   = ["glue:GetJobRun"]
    resources = [aws_glue_job.bronze.arn]
  }
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/logs/execucoes/*"]
  }
}

resource "aws_iam_role_policy" "job_concluido_permissions" {
  role   = aws_iam_role.job_concluido_lambda.id
  policy = data.aws_iam_policy_document.job_concluido_permissions.json
}

resource "aws_lambda_function" "job_concluido" {
  function_name    = "${var.prefix}-job-concluido"
  role             = aws_iam_role.job_concluido_lambda.arn
  handler          = "job_concluido.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 128
  filename         = data.archive_file.job_concluido_lambda.output_path
  source_code_hash = data.archive_file.job_concluido_lambda.output_base64sha256

  environment {
    variables = {
      # O evento do Glue não traz o nome do bucket -- diferente das
      # Lambdas disparadas por evento do S3, que já vem no detail.
      BUCKET = aws_s3_bucket.lake.id
    }
  }
}

resource "aws_cloudwatch_event_rule" "glue_bronze_concluido" {
  name        = "${var.prefix}-glue-bronze-concluido"
  description = "Job Glue Bronze terminou, qualquer desfecho -- grava na tabela de controle"

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
    arn = aws_sqs_queue.eventos_falhos.arn
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
