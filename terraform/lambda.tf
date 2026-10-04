# Lambda que descompacta o Cumulative do DTCC. Invocada pela state
# machine (ver terraform/step_functions.tf), não mais diretamente pelo
# S3 -- até 03/10/2026 era a Lambda alvo direto da notificação do
# bucket; virou um Task da state machine quando o pipeline passou a ser
# orquestrado por Step Functions (ver observabilidade.tf -> não, ver
# step_functions.tf), pra poder esperar o Glue terminar e coordenar com
# a checagem de qualidade sem precisar de mais regras do EventBridge.
#
# O provider "archive" (usado abaixo para empacotar o código Python) é
# declarado em main.tf -- Terraform só aceita 1 bloco required_providers
# por módulo, não um por arquivo.

data "archive_file" "unzip_lambda" {
  type        = "zip"
  source_file = "${path.module}/../lambda/unzip_dtcc.py"
  output_path = "${path.module}/.build/unzip_dtcc.zip"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "unzip_lambda" {
  name               = "${var.prefix}-unzip-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "unzip_lambda_logs" {
  role       = aws_iam_role.unzip_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "unzip_lambda_s3" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.lake.arn}/${local.fonte.zip_prefix}*"]
  }
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/${local.fonte.raw_prefix}*"]
  }
  statement {
    # Tabela de controle (logs/execucoes/, ver athena/queries.sql) -- o
    # primeiro registro do fluxo end-to-end, gravado na descompactação.
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/logs/execucoes/*"]
  }
}

resource "aws_iam_role_policy" "unzip_lambda_s3" {
  role   = aws_iam_role.unzip_lambda.id
  policy = data.aws_iam_policy_document.unzip_lambda_s3.json
}

resource "aws_lambda_function" "unzip_dtcc" {
  function_name    = "${var.prefix}-unzip-dtcc"
  role             = aws_iam_role.unzip_lambda.arn
  handler          = "unzip_dtcc.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.unzip_lambda.output_path
  source_code_hash = data.archive_file.unzip_lambda.output_base64sha256

  environment {
    variables = {
      # Pra onde escrever o .csv descompactado -- vem do contrato de
      # fonte (config/fontes/dtcc.yaml), não cravado no código Python.
      RAW_PREFIX = local.fonte.raw_prefix
    }
  }
}

# ---------- Notificação do bucket ----------
# Só manda os eventos pro EventBridge -- não invoca nenhuma Lambda
# diretamente. Antes (até 03/10/2026) havia um `lambda_function` aqui
# invocando unzip_dtcc direto no upload do .zip; virou a regra
# `zip_arrived` em step_functions.tf, que invoca uma Lambda que inicia a
# state machine com nome de execução determinístico (ver
# lambda/iniciar_pipeline.py) -- proteção contra entrega duplicada do
# evento que um `lambda_function` direto aqui não teria.
resource "aws_s3_bucket_notification" "unzip_on_upload" {
  bucket      = aws_s3_bucket.lake.id
  eventbridge = true
}

output "lambda_unzip" {
  value = aws_lambda_function.unzip_dtcc.function_name
}
