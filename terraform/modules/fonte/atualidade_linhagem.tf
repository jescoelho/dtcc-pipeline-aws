# Checagem agendada (não reativa a evento) de atualidade e linhagem
# desta fonte -- ver lambda/checar_pipeline.py e docs/DECISOES.md pelo
# raciocínio completo.

data "archive_file" "checar_pipeline_lambda" {
  type        = "zip"
  source_file = "${path.module}/../../../lambda/checar_pipeline.py"
  output_path = "${path.module}/../../.build/${var.fonte.nome}_checar_pipeline.zip"
}

resource "aws_iam_role" "checar_pipeline_lambda" {
  name               = "${local.nome}-checar-pipeline-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "checar_pipeline_lambda_logs" {
  role       = aws_iam_role.checar_pipeline_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "checar_pipeline_permissions" {
  statement {
    # Consulta controle_execucoes via Athena em vez de listar/ler
    # logs/execucoes/ arquivo por arquivo -- ver _consultar_athena em
    # lambda/checar_pipeline.py.
    actions   = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults"]
    resources = [var.athena_workgroup_arn]
  }
  statement {
    # Athena resolve a tabela via Glue Data Catalog por baixo.
    actions = ["glue:GetTable", "glue:GetDatabase", "glue:GetPartitions"]
    resources = [
      var.glue_catalog_database_arn,
      "arn:aws:glue:${var.region}:${var.account_id}:catalog",
      "arn:aws:glue:${var.region}:${var.account_id}:table/${var.glue_catalog_database_name}/*",
    ]
  }
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${var.bucket_arn}/logs/execucoes/*"]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["logs/execucoes/*", "athena-results/*"]
    }
  }
  statement {
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${var.bucket_arn}/athena-results/*"]
  }
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${var.bucket_arn}/logs/execucoes/*"]
  }
  statement {
    # Checagem de execuções travadas (_checar_execucoes_travadas) --
    # cruza direto com o Step Functions desta fonte.
    actions   = ["states:ListExecutions"]
    resources = [aws_sfn_state_machine.pipeline.arn]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [var.sns_topic_arn]
  }
}

resource "aws_iam_role_policy" "checar_pipeline_permissions" {
  role   = aws_iam_role.checar_pipeline_lambda.id
  policy = data.aws_iam_policy_document.checar_pipeline_permissions.json
}

resource "aws_lambda_function" "checar_pipeline" {
  function_name    = "${local.nome}-checar-pipeline"
  role             = aws_iam_role.checar_pipeline_lambda.arn
  handler          = "checar_pipeline.handler"
  runtime          = "python3.12"
  timeout          = 90
  memory_size      = 256
  filename         = data.archive_file.checar_pipeline_lambda.output_path
  source_code_hash = data.archive_file.checar_pipeline_lambda.output_base64sha256

  environment {
    variables = {
      BUCKET            = var.bucket_name
      SNS_TOPIC_ARN     = var.sns_topic_arn
      DATABASE          = var.glue_catalog_database_name
      WORKGROUP         = var.athena_workgroup_name
      STATE_MACHINE_ARN = aws_sfn_state_machine.pipeline.arn
      # Vêm do contrato de fonte.
      JANELA_LINHAGEM_DIAS  = tostring(var.fonte.janela_linhagem_dias)
      MARGEM_LINHAGEM_HORAS = tostring(var.fonte.margem_linhagem_horas)
      NOME_FONTE            = var.fonte.nome
      ETAPAS_ESPERADAS      = join(",", var.fonte.etapas_esperadas)
    }
  }
}

resource "aws_cloudwatch_event_rule" "checar_pipeline_agenda" {
  name                = "${local.nome}-checar-pipeline-agenda"
  description         = "Dispara a checagem de atualidade/linhagem desta fonte uma vez por dia útil"
  schedule_expression = var.fonte.checagem_cron
}

resource "aws_cloudwatch_event_target" "checar_pipeline_agenda_to_lambda" {
  rule      = aws_cloudwatch_event_rule.checar_pipeline_agenda.name
  target_id = "checar-pipeline"
  arn       = aws_lambda_function.checar_pipeline.arn

  dead_letter_config {
    arn = var.dlq_arn
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
output "checar_pipeline_agenda_rule_arn" {
  value = aws_cloudwatch_event_rule.checar_pipeline_agenda.arn
}
