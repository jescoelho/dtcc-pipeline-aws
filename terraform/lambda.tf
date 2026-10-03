# Lambda que descompacta o Cumulative do DTCC, disparada quando um .zip
# cai em raw/dtcc_zip/. Escopo desta etapa: só descompactar, não dispara
# o Glue ainda (ver lambda/unzip_dtcc.py).

terraform {
  required_providers {
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

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

resource "aws_s3_bucket_notification" "unzip_on_upload" {
  bucket = aws_s3_bucket.lake.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.unzip_dtcc.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "raw/dtcc_zip/"
    filter_suffix       = ".zip"
  }

  depends_on = [aws_lambda_permission.allow_s3]
}

output "lambda_unzip" {
  value = aws_lambda_function.unzip_dtcc.function_name
}
