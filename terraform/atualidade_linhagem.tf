# Checagem agendada (NÃO reativa a evento) de atualidade e linhagem --
# ver lambda/checar_pipeline.py pro raciocínio completo e README.md,
# seção desta etapa.
#
# Diferença de desenho em relação a TODAS as outras Lambdas deste
# pipeline (unzip_dtcc, quality_check, job_concluido, iniciar_pipeline):
# aquelas reagem a um evento que aconteceu (um arquivo chegou, um job
# terminou). Esta aqui precisa rodar sozinha, numa agenda fixa, porque
# o que ela procura é a AUSÊNCIA de um evento esperado -- não existe
# notificação nativa da AWS para "o dia útil terminou e nada chegou" ou
# "uma execução começou e nunca mais apareceu na tabela de controle".
#
# aws_cloudwatch_event_rule com schedule_expression (em vez de
# event_pattern) -- mesmo tipo de recurso já usado nas regras reativas
# deste módulo, só que disparado pelo relógio. Só dias úteis (MON-FRI)
# -- o DTCC não publica fim de semana (ver README, 03/10/2026), então
# rodar sábado/domingo só repetiria a checagem de sexta-feira sem
# necessidade.

data "archive_file" "checar_pipeline_lambda" {
  type        = "zip"
  source_file = "${path.module}/../lambda/checar_pipeline.py"
  output_path = "${path.module}/.build/checar_pipeline.zip"
}

resource "aws_iam_role" "checar_pipeline_lambda" {
  name               = "${var.prefix}-checar-pipeline-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "checar_pipeline_lambda_logs" {
  role       = aws_iam_role.checar_pipeline_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "checar_pipeline_permissions" {
  statement {
    # Lê a tabela de controle inteira (últimos JANELA_LINHAGEM_DIAS
    # dias) -- diferente de quality_check.py, que só lê pra calcular
    # uma média, esta Lambda precisa enxergar todas as etapas de cada
    # execution_id.
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
  statement {
    # Grava o próprio veredito (origem="checar_pipeline") na mesma
    # tabela.
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/logs/execucoes/*"]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alertas.arn]
  }
}

resource "aws_iam_role_policy" "checar_pipeline_permissions" {
  role   = aws_iam_role.checar_pipeline_lambda.id
  policy = data.aws_iam_policy_document.checar_pipeline_permissions.json
}

resource "aws_lambda_function" "checar_pipeline" {
  function_name    = "${var.prefix}-checar-pipeline"
  role             = aws_iam_role.checar_pipeline_lambda.arn
  handler          = "checar_pipeline.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.checar_pipeline_lambda.output_path
  source_code_hash = data.archive_file.checar_pipeline_lambda.output_base64sha256

  environment {
    variables = {
      BUCKET        = aws_s3_bucket.lake.id
      SNS_TOPIC_ARN = aws_sns_topic.alertas.arn
      # Vêm do contrato de fonte (config/fontes/dtcc.yaml), mesmo padrão
      # de DIAS_HISTORICO/QUEDA_MAXIMA_TOLERADA em quality_check.tf.
      JANELA_LINHAGEM_DIAS  = tostring(local.fonte.janela_linhagem_dias)
      MARGEM_LINHAGEM_HORAS = tostring(local.fonte.margem_linhagem_horas)
    }
  }
}

resource "aws_cloudwatch_event_rule" "checar_pipeline_agenda" {
  name                = "${var.prefix}-checar-pipeline-agenda"
  description         = "Dispara a checagem de atualidade/linhagem uma vez por dia útil"
  schedule_expression = "cron(0 11 ? * MON-FRI *)"
}

resource "aws_cloudwatch_event_target" "checar_pipeline_agenda_to_lambda" {
  rule      = aws_cloudwatch_event_rule.checar_pipeline_agenda.name
  target_id = "checar-pipeline"
  arn       = aws_lambda_function.checar_pipeline.arn

  dead_letter_config {
    arn = aws_sqs_queue.eventos_falhos.arn
  }

  retry_policy {
    maximum_retry_attempts       = 3
    maximum_event_age_in_seconds = 3600
  }
}

resource "aws_lambda_permission" "allow_eventbridge_checar_pipeline" {
  statement_id  = "AllowEventBridgeInvokeCheckPipeline"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.checar_pipeline.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.checar_pipeline_agenda.arn
}

output "lambda_checar_pipeline" {
  value = aws_lambda_function.checar_pipeline.function_name
}
