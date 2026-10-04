# Lambda que confere qualidade básica do CSV desta fonte (volume contra
# o histórico, simples e sazonal) -- ver lambda/quality_check.py e
# docs/DECISOES.md. Invocada por um branch em paralelo da state machine
# (ver step_functions.tf). Se achar problema, publica no tópico SNS
# compartilhado (var.sns_topic_arn).

data "archive_file" "quality_check_lambda" {
  type        = "zip"
  source_file = "${path.module}/../../../lambda/quality_check.py"
  output_path = "${path.module}/../../.build/${var.fonte.nome}_quality_check.zip"
}

resource "aws_iam_role" "quality_check_lambda" {
  name               = "${local.nome}-quality-check-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "quality_check_lambda_logs" {
  role       = aws_iam_role.quality_check_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "quality_check_lambda_permissions" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${var.bucket_arn}/${var.fonte.raw_prefix}*"]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [var.sns_topic_arn]
  }
  statement {
    # Tabela de controle: grava o próprio veredito e lê o histórico
    # (simples e sazonal, ver _escolher_baseline em
    # lambda/quality_check.py) pra comparar o volume de hoje.
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${var.bucket_arn}/logs/execucoes/*"]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["logs/execucoes/*"]
    }
  }
}

resource "aws_iam_role_policy" "quality_check_lambda_permissions" {
  role   = aws_iam_role.quality_check_lambda.id
  policy = data.aws_iam_policy_document.quality_check_lambda_permissions.json
}

resource "aws_lambda_function" "quality_check" {
  function_name = "${local.nome}-quality-check"
  role          = aws_iam_role.quality_check_lambda.arn
  handler       = "quality_check.handler"
  runtime       = "python3.12"
  # 128 MB estourava com Runtime.OutOfMemory no arquivo real do DTCC
  # (ver docs/DECISOES.md) -- 1024/60s é o que funcionou pra essa fonte;
  # uma fonte com arquivos bem menores poderia usar menos, mas não vale
  # a pena virar campo do contrato só por isto ainda (nenhuma segunda
  # fonte real pra calibrar contra).
  timeout          = 60
  memory_size      = 1024
  filename         = data.archive_file.quality_check_lambda.output_path
  source_code_hash = data.archive_file.quality_check_lambda.output_base64sha256

  environment {
    variables = {
      SNS_TOPIC_ARN = var.sns_topic_arn
      # Comparação de volume contra o histórico (ver
      # lambda/quality_check.py) -- tudo vem do contrato de fonte.
      DIAS_HISTORICO            = tostring(var.fonte.dias_historico)
      QUEDA_MAXIMA_TOLERADA     = tostring(var.fonte.queda_maxima_tolerada)
      CONSISTENCIA_SAZONAL      = tostring(var.fonte.consistencia_sazonal)
      SEMANAS_HISTORICO_SAZONAL = tostring(var.fonte.semanas_historico_sazonal)
      NOME_FONTE                = var.fonte.nome
    }
  }
}

# Sem aws_lambda_permission: quem invoca esta Lambda é a state machine
# (Task "ChecarQualidade" em step_functions.tf), via identidade (role
# da state machine), não por notificação/evento nativo.

output "lambda_quality_check" {
  value = aws_lambda_function.quality_check.function_name
}
