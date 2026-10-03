# Lambda que confere qualidade básica do CSV (schema, volume, domínio de
# Action type) quando ele aparece em raw/dtcc/ -- ver lambda/quality_check.py
# para o raciocínio completo e README.md, seção "Checagem de qualidade de
# dados".
#
# Reage ao MESMO evento do S3 que a trigger_bronze (ver lambda.tf), mas
# como alvo independente -- roda em paralelo, não bloqueia o Glue. Se
# achar problema, publica no tópico SNS que já existe para falha do Glue
# (observabilidade.tf): um só canal de alerta para "pipeline com
# problema", não importa a causa.

data "archive_file" "quality_check_lambda" {
  type        = "zip"
  source_file = "${path.module}/../lambda/quality_check.py"
  output_path = "${path.module}/.build/quality_check.zip"
}

resource "aws_iam_role" "quality_check_lambda" {
  name               = "${var.prefix}-quality-check-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "quality_check_lambda_logs" {
  role       = aws_iam_role.quality_check_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "quality_check_lambda_permissions" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.lake.arn}/raw/dtcc/*"]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alertas.arn]
  }
}

resource "aws_iam_role_policy" "quality_check_lambda_permissions" {
  role   = aws_iam_role.quality_check_lambda.id
  policy = data.aws_iam_policy_document.quality_check_lambda_permissions.json
}

resource "aws_lambda_function" "quality_check" {
  function_name    = "${var.prefix}-quality-check"
  role              = aws_iam_role.quality_check_lambda.arn
  handler           = "quality_check.handler"
  runtime           = "python3.12"
  timeout           = 30
  memory_size       = 128
  filename          = data.archive_file.quality_check_lambda.output_path
  source_code_hash  = data.archive_file.quality_check_lambda.output_base64sha256

  environment {
    variables = {
      SNS_TOPIC_ARN = aws_sns_topic.alertas.arn
    }
  }
}

resource "aws_lambda_permission" "allow_s3_quality_check" {
  statement_id  = "AllowS3InvokeQualityCheck"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.quality_check.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.lake.arn
}

output "lambda_quality_check" {
  value = aws_lambda_function.quality_check.function_name
}
