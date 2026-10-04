# Ingestão agendada do arquivo de origem desta fonte -- ver
# lambda/ingerir_cumulative.py e docs/DECISOES.md ("Ingestão agendada").
# Nome da Lambda/arquivo continua "ingerir_cumulative"/"Cumulative" por
# ora -- é específico do relatório Cumulative do DTCC (ver limite
# assumido em config/fontes/dtcc.yaml); uma segunda fonte com um
# relatório de origem diferente precisaria de uma Lambda de ingestão
# própria, não reaproveitável como as demais.

data "archive_file" "ingerir_cumulative_lambda" {
  type        = "zip"
  source_file = "${path.module}/../../../lambda/ingerir_cumulative.py"
  output_path = "${path.module}/../../.build/${var.fonte.nome}_ingerir_cumulative.zip"
}

resource "aws_iam_role" "ingerir_cumulative_lambda" {
  name               = "${local.nome}-ingerir-cumulative-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "ingerir_cumulative_lambda_logs" {
  role       = aws_iam_role.ingerir_cumulative_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "ingerir_cumulative_lambda_permissions" {
  statement {
    # Bucket público do DTCC, fora da nossa conta -- ver nota completa
    # em docs/DECISOES.md.
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${var.fonte.origem_bucket}/*"]
  }
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${var.bucket_arn}/${var.fonte.zip_prefix}*"]
  }
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${var.bucket_arn}/logs/execucoes/*"]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [var.sns_topic_arn]
  }
}

resource "aws_iam_role_policy" "ingerir_cumulative_lambda_permissions" {
  role   = aws_iam_role.ingerir_cumulative_lambda.id
  policy = data.aws_iam_policy_document.ingerir_cumulative_lambda_permissions.json
}

resource "aws_lambda_function" "ingerir_cumulative" {
  function_name    = "${local.nome}-ingerir"
  role             = aws_iam_role.ingerir_cumulative_lambda.arn
  handler          = "ingerir_cumulative.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.ingerir_cumulative_lambda.output_path
  source_code_hash = data.archive_file.ingerir_cumulative_lambda.output_base64sha256

  environment {
    variables = {
      ORIGEM_BUCKET        = var.fonte.origem_bucket
      ORIGEM_PADRAO        = var.fonte.origem_padrao
      ZIP_PREFIX           = var.fonte.zip_prefix
      CLASSE_ATIVO_DEFAULT = var.fonte.origem_classe_ativo_default
      FONTE_DEFAULT        = var.fonte.origem_fonte_default
      BUCKET               = var.bucket_name
      SNS_TOPIC_ARN        = var.sns_topic_arn
      NOME_FONTE           = var.fonte.nome
    }
  }
}

resource "aws_cloudwatch_event_rule" "ingestao_agendada" {
  name                = "${local.nome}-ingestao-agendada"
  description         = "Busca o arquivo de hoje desta fonte direto do bucket de origem, uma vez por dia útil"
  schedule_expression = var.fonte.ingestao_cron
}

resource "aws_cloudwatch_event_target" "ingestao_agendada_to_lambda" {
  rule      = aws_cloudwatch_event_rule.ingestao_agendada.name
  target_id = "ingerir-cumulative"
  arn       = aws_lambda_function.ingerir_cumulative.arn

  dead_letter_config {
    arn = var.dlq_arn
  }

  retry_policy {
    maximum_retry_attempts       = 3
    maximum_event_age_in_seconds = 3600
  }
}

resource "aws_lambda_permission" "allow_eventbridge_ingerir_cumulative" {
  statement_id  = "AllowEventBridgeInvokeIngerirCumulative"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingerir_cumulative.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ingestao_agendada.arn
}

output "lambda_ingerir_cumulative" {
  value = aws_lambda_function.ingerir_cumulative.function_name
}
output "ingestao_agendada_rule_arn" {
  value = aws_cloudwatch_event_rule.ingestao_agendada.arn
}
