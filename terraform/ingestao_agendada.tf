# Ingestão agendada do Cumulative do DTCC -- substitui o único passo
# manual que restava no pipeline (scripts/ingerir_cumulative.sh, rodado
# à mão, ver "Tarefas futuras" no README). Reimplementa a mesma cópia
# servidor-a-servidor (lambda/ingerir_cumulative.py) como Lambda,
# disparada por uma agenda fixa do EventBridge -- mesmo raciocínio já
# aplicado à checagem de atualidade/linhagem (terraform/atualidade_linhagem.tf):
# não existe evento nativo da AWS para "está na hora de buscar o arquivo
# de hoje", só um relógio.
#
# scripts/ingerir_cumulative.sh continua existindo -- útil pra um
# backfill manual (reprocessar uma data específica ou classe de ativo
# diferente) sem esperar o agendamento, ou antes de a Lambda existir
# numa conta nova. Ver docs/DECISOES.md para o raciocínio completo e o
# limite assumido sobre o horário do cron (horário real de publicação
# do DTCC não é documentado oficialmente).

data "archive_file" "ingerir_cumulative_lambda" {
  type        = "zip"
  source_file = "${path.module}/../lambda/ingerir_cumulative.py"
  output_path = "${path.module}/.build/ingerir_cumulative.zip"
}

resource "aws_iam_role" "ingerir_cumulative_lambda" {
  name               = "${var.prefix}-ingerir-cumulative-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "ingerir_cumulative_lambda_logs" {
  role       = aws_iam_role.ingerir_cumulative_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "ingerir_cumulative_lambda_permissions" {
  statement {
    # Bucket público do DTCC, fora da nossa conta -- s3:GetObject aqui só
    # autoriza NOSSA role a chamar a API; quem precisa permitir a leitura
    # pública é a política do bucket de origem, do lado do DTCC (já
    # permite -- confirmado manualmente, ver scripts/ingerir_cumulative.sh).
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${local.fonte.origem_bucket}/*"]
  }
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/${local.fonte.zip_prefix}*"]
  }
  statement {
    # Tabela de controle (logs/execucoes/, ver athena/queries.sql) --
    # mesmo padrão das outras Lambdas: registra o próprio resultado da
    # cópia (origem="ingerir_cumulative").
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/logs/execucoes/*"]
  }
  statement {
    # Alerta direto por SNS se a cópia falhar -- não existe evento
    # nativo de "CopyObject falhou entre dois buckets" pra uma regra do
    # EventBridge escutar (diferente do Glue, que emite "Job State
    # Change"), então o alerta é publicado do próprio código.
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alertas.arn]
  }
}

resource "aws_iam_role_policy" "ingerir_cumulative_lambda_permissions" {
  role   = aws_iam_role.ingerir_cumulative_lambda.id
  policy = data.aws_iam_policy_document.ingerir_cumulative_lambda_permissions.json
}

resource "aws_lambda_function" "ingerir_cumulative" {
  function_name    = "${var.prefix}-ingerir-${local.fonte.nome}"
  role             = aws_iam_role.ingerir_cumulative_lambda.arn
  handler          = "ingerir_cumulative.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.ingerir_cumulative_lambda.output_path
  source_code_hash = data.archive_file.ingerir_cumulative_lambda.output_base64sha256

  environment {
    variables = {
      # Todos vêm do contrato de fonte (config/fontes/dtcc.yaml) --
      # mesmo padrão já usado pelas outras Lambdas (ver
      # docs/DECISOES.md, "Contrato de fonte").
      ORIGEM_BUCKET        = local.fonte.origem_bucket
      ORIGEM_PADRAO        = local.fonte.origem_padrao
      ZIP_PREFIX           = local.fonte.zip_prefix
      CLASSE_ATIVO_DEFAULT = local.fonte.origem_classe_ativo_default
      FONTE_DEFAULT        = local.fonte.origem_fonte_default
      BUCKET               = aws_s3_bucket.lake.id
      SNS_TOPIC_ARN        = aws_sns_topic.alertas.arn
      NOME_FONTE           = local.fonte.nome
    }
  }
}

resource "aws_cloudwatch_event_rule" "ingestao_agendada" {
  name                = "${var.prefix}-ingestao-agendada"
  description         = "Busca o Cumulative do dia direto do bucket do DTCC, uma vez por dia útil"
  schedule_expression = local.fonte.ingestao_cron
}

resource "aws_cloudwatch_event_target" "ingestao_agendada_to_lambda" {
  rule      = aws_cloudwatch_event_rule.ingestao_agendada.name
  target_id = "ingerir-cumulative"
  arn       = aws_lambda_function.ingerir_cumulative.arn

  dead_letter_config {
    arn = aws_sqs_queue.eventos_falhos.arn
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
