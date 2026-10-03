# Lambda que descompacta o Cumulative do DTCC, disparada quando um .zip
# cai em raw/dtcc_zip/. Escopo desta etapa: só descompactar, não dispara
# o Glue ainda (ver lambda/unzip_dtcc.py).
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
    resources = ["${aws_s3_bucket.lake.arn}/raw/dtcc_zip/*"]
  }
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/raw/dtcc/*"]
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
}

resource "aws_lambda_permission" "allow_s3" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.unzip_dtcc.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.lake.arn
}

# ---------- Lambda 2/2: dispara o Glue Bronze quando o .csv aparece ----------
# Responsabilidade separada da Lambda de descompactar, de propósito (ver
# README.md, seção "Opção 2"): se o start_job_run falhar, isso não deve
# ser confundido com uma falha na descompactação, que já funcionou; e dá
# pra reagir ao mesmo evento com outras coisas (checagem de qualidade,
# notificação -- tarefas futuras) sem tocar nesta Lambda.
data "archive_file" "trigger_bronze_lambda" {
  type        = "zip"
  source_file = "${path.module}/../lambda/trigger_bronze.py"
  output_path = "${path.module}/.build/trigger_bronze.zip"
}

resource "aws_iam_role" "trigger_bronze_lambda" {
  name               = "${var.prefix}-trigger-bronze-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "trigger_bronze_lambda_logs" {
  role       = aws_iam_role.trigger_bronze_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "trigger_bronze_glue" {
  statement {
    actions   = ["glue:StartJobRun"]
    resources = [aws_glue_job.bronze.arn]
  }
}

resource "aws_iam_role_policy" "trigger_bronze_glue" {
  role   = aws_iam_role.trigger_bronze_lambda.id
  policy = data.aws_iam_policy_document.trigger_bronze_glue.json
}

resource "aws_lambda_function" "trigger_bronze" {
  function_name    = "${var.prefix}-trigger-bronze"
  role             = aws_iam_role.trigger_bronze_lambda.arn
  handler          = "trigger_bronze.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 128
  filename         = data.archive_file.trigger_bronze_lambda.output_path
  source_code_hash = data.archive_file.trigger_bronze_lambda.output_base64sha256

  environment {
    variables = {
      GLUE_JOB_NAME = aws_glue_job.bronze.name
    }
  }
}

resource "aws_lambda_permission" "allow_s3_trigger_bronze" {
  statement_id  = "AllowS3InvokeTriggerBronze"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.trigger_bronze.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.lake.arn
}

# Uma única configuração de notificação para o bucket -- o S3 não aceita
# mais de um aws_s3_bucket_notification por bucket, então os dois gatilhos
# (descompactar e disparar o Glue) vivem juntos aqui, cada um com seu
# prefixo/sufixo próprio.
resource "aws_s3_bucket_notification" "unzip_on_upload" {
  bucket = aws_s3_bucket.lake.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.unzip_dtcc.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "raw/dtcc_zip/"
    filter_suffix       = ".zip"
  }

  lambda_function {
    lambda_function_arn = aws_lambda_function.trigger_bronze.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "raw/dtcc/"
    filter_suffix       = ".csv"
  }

  depends_on = [aws_lambda_permission.allow_s3, aws_lambda_permission.allow_s3_trigger_bronze]
}

output "lambda_unzip" {
  value = aws_lambda_function.unzip_dtcc.function_name
}
output "lambda_trigger_bronze" {
  value = aws_lambda_function.trigger_bronze.function_name
}
