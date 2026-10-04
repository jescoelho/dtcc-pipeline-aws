# Lambda que descompacta o .zip desta fonte. Invocada pela state
# machine (ver step_functions.tf), não diretamente por notificação do
# bucket -- a notificação (`eventbridge = true`) é configurada uma
# única vez no bucket inteiro, no root (../main.tf), porque um bucket
# só aceita uma aws_s3_bucket_notification; cada fonte reage ao mesmo
# EventBridge por regra própria (zip_arrived, em step_functions.tf),
# filtrando pelo seu zip_prefix.
#
# O provider "archive" é declarado no root -- Terraform só aceita 1
# bloco required_providers por módulo raiz, não por módulo filho.

data "archive_file" "unzip_lambda" {
  type        = "zip"
  source_file = "${path.module}/../../../lambda/unzip_dtcc.py"
  output_path = "${path.module}/../../.build/${var.fonte.nome}_unzip_dtcc.zip"
}

resource "aws_iam_role" "unzip_lambda" {
  name               = "${local.nome}-unzip-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "unzip_lambda_logs" {
  role       = aws_iam_role.unzip_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "unzip_lambda_s3" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${var.bucket_arn}/${var.fonte.zip_prefix}*"]
  }
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${var.bucket_arn}/${var.fonte.raw_prefix}*"]
  }
  statement {
    # Tabela de controle (logs/execucoes/, compartilhada entre fontes --
    # a coluna "origem" de cada registro é o que distingue uma da
    # outra, não um prefixo por fonte).
    actions   = ["s3:PutObject"]
    resources = ["${var.bucket_arn}/logs/execucoes/*"]
  }
}

resource "aws_iam_role_policy" "unzip_lambda_s3" {
  role   = aws_iam_role.unzip_lambda.id
  policy = data.aws_iam_policy_document.unzip_lambda_s3.json
}

resource "aws_lambda_function" "unzip_dtcc" {
  function_name    = "${local.nome}-unzip"
  role             = aws_iam_role.unzip_lambda.arn
  handler          = "unzip_dtcc.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.unzip_lambda.output_path
  source_code_hash = data.archive_file.unzip_lambda.output_base64sha256

  environment {
    variables = {
      RAW_PREFIX = var.fonte.raw_prefix
    }
  }
}

output "lambda_unzip" {
  value = aws_lambda_function.unzip_dtcc.function_name
}
