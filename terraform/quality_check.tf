# Lambda que confere qualidade básica do CSV (volume contra o histórico)
# quando ele aparece em raw/dtcc/ -- ver lambda/quality_check.py para o
# raciocínio completo e README.md, seção "Checagem de qualidade de
# dados".
#
# Invocada por um branch em paralelo da state machine (ver
# terraform/step_functions.tf), não mais por uma regra própria do
# EventBridge -- roda ao lado do Glue, não bloqueia nem depende dele. Se
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
    resources = ["${aws_s3_bucket.lake.arn}/${local.fonte.raw_prefix}*"]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alertas.arn]
  }
  statement {
    # Tabela de controle (logs/execucoes/, ver athena/queries.sql) -- um
    # registro por checagem, consultável via SQL, diferente do log do
    # CloudWatch.
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/logs/execucoes/*"]
  }
  statement {
    # Lê o próprio histórico (últimos N dias) pra comparar o volume de
    # hoje com a média -- extensão da checagem de qualidade (ver
    # _media_historica em lambda/quality_check.py).
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.lake.arn}/logs/execucoes/*"]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn]
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
  function_name    = "${var.prefix}-quality-check"
  role              = aws_iam_role.quality_check_lambda.arn
  handler           = "quality_check.handler"
  runtime           = "python3.12"
  # 128 MB estourava com Runtime.OutOfMemory no arquivo real (achado em
  # produção, 2026-10-03): o código carrega o CSV inteiro na memória
  # (_checar le o Body todo e decodifica pra string), e o relatório
  # "Cumulative" do DTCC cresce com o tempo -- acumula posições abertas,
  # não é um tamanho fixo. 1024 MB dá margem; timeout subiu de 30 pra 60s
  # porque ler/decodificar um arquivo maior também leva mais tempo (CPU
  # no Lambda escala com a memória, então não é proporcionalmente mais
  # lento). Limite real: ainda é O(tamanho do arquivo) de memória -- se o
  # Cumulative continuar crescendo, isso volta a estourar em algum
  # momento. Correção definitiva seria processar em streaming
  # (iter_lines no Body, sem carregar tudo de uma vez), não construída
  # agora.
  timeout           = 60
  memory_size       = 1024
  filename          = data.archive_file.quality_check_lambda.output_path
  source_code_hash  = data.archive_file.quality_check_lambda.output_base64sha256

  environment {
    variables = {
      SNS_TOPIC_ARN = aws_sns_topic.alertas.arn
      # Comparação de volume contra o histórico (ver _media_historica em
      # lambda/quality_check.py) -- vem do contrato de fonte
      # (config/fontes/dtcc.yaml), não cravado no código Python.
      DIAS_HISTORICO        = tostring(local.fonte.dias_historico)
      QUEDA_MAXIMA_TOLERADA = tostring(local.fonte.queda_maxima_tolerada)
    }
  }
}

# Não precisa de aws_lambda_permission aqui: quem invoca esta Lambda
# agora é a state machine (Task "ChecarQualidade" em
# terraform/step_functions.tf), via API (sts:AssumeRole do papel da
# state machine + lambda:InvokeFunction na política dele) -- permissão
# baseada em identidade, não em política de recurso. `aws_lambda_permission`
# só é necessário quando um serviço invoca via notificação/evento
# nativo (S3, EventBridge), que não é mais o caso desta Lambda.

output "lambda_quality_check" {
  value = aws_lambda_function.quality_check.function_name
}
