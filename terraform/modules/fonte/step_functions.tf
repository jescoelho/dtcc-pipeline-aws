# Orquestração do pipeline Bronze desta fonte via Step Functions -- ver
# docs/DECISOES.md, "Step Functions -- orquestração do pipeline Bronze",
# pelo raciocínio completo (proteção contra entrega duplicada do S3,
# nome de execução determinístico via lambda/iniciar_pipeline.py).

# ---------- Lambda que inicia a execução ----------

data "archive_file" "iniciar_pipeline_lambda" {
  type        = "zip"
  source_file = "${path.module}/../../../lambda/iniciar_pipeline.py"
  output_path = "${path.module}/../../.build/${var.fonte.nome}_iniciar_pipeline.zip"
}

resource "aws_iam_role" "iniciar_pipeline_lambda" {
  name               = "${local.nome}-iniciar-pipeline-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "iniciar_pipeline_lambda_logs" {
  role       = aws_iam_role.iniciar_pipeline_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "iniciar_pipeline_permissions" {
  statement {
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.pipeline.arn]
  }
}

resource "aws_iam_role_policy" "iniciar_pipeline_permissions" {
  role   = aws_iam_role.iniciar_pipeline_lambda.id
  policy = data.aws_iam_policy_document.iniciar_pipeline_permissions.json
}

resource "aws_lambda_function" "iniciar_pipeline" {
  function_name    = "${local.nome}-iniciar-pipeline"
  role             = aws_iam_role.iniciar_pipeline_lambda.arn
  handler          = "iniciar_pipeline.handler"
  runtime          = "python3.12"
  timeout          = 15
  memory_size      = 128
  filename         = data.archive_file.iniciar_pipeline_lambda.output_path
  source_code_hash = data.archive_file.iniciar_pipeline_lambda.output_base64sha256

  environment {
    variables = {
      STATE_MACHINE_ARN = aws_sfn_state_machine.pipeline.arn
    }
  }
}

# ---------- Gatilho: .zip desta fonte chegou ----------
# O bucket manda TODO evento de criação de objeto pro EventBridge (uma
# vez só, no root) -- esta regra filtra pelo zip_prefix desta fonte, pra
# cada instância do módulo só reagir ao seu próprio .zip.
resource "aws_cloudwatch_event_rule" "zip_arrived" {
  name        = "${local.nome}-zip-arrived"
  description = "Zip desta fonte chegou -- inicia a state machine do pipeline"

  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [var.bucket_name] }
      object = {
        key = [
          { prefix = var.fonte.zip_prefix },
        ]
      }
    }
  })
}

resource "aws_cloudwatch_event_target" "zip_arrived_to_iniciar_pipeline" {
  rule      = aws_cloudwatch_event_rule.zip_arrived.name
  target_id = "iniciar-pipeline"
  arn       = aws_lambda_function.iniciar_pipeline.arn

  dead_letter_config {
    arn = var.dlq_arn
  }

  retry_policy {
    maximum_retry_attempts       = 3
    maximum_event_age_in_seconds = 3600
  }
}

resource "aws_lambda_permission" "allow_eventbridge_iniciar_pipeline" {
  statement_id  = "AllowEventBridgeInvokeIniciarPipeline"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.iniciar_pipeline.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.zip_arrived.arn
}

# ---------- State machine ----------

data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn_pipeline" {
  name               = "${local.nome}-sfn-pipeline"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
}

data "aws_iam_policy_document" "sfn_pipeline_permissions" {
  statement {
    actions = ["lambda:InvokeFunction"]
    resources = [
      aws_lambda_function.unzip_dtcc.arn,
      aws_lambda_function.quality_check.arn,
    ]
  }
  statement {
    # O Glue não suporta política no nível de recurso pra estas ações
    # (confirmado contra documentação oficial da AWS) -- Resource "*"
    # aqui não é um relaxamento feito por conveniência.
    actions = [
      "glue:StartJobRun",
      "glue:GetJobRun",
      "glue:GetJobRuns",
      "glue:BatchStopJobRun",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "sfn_pipeline_permissions" {
  role   = aws_iam_role.sfn_pipeline.id
  policy = data.aws_iam_policy_document.sfn_pipeline_permissions.json
}

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${local.nome}-pipeline"
  role_arn = aws_iam_role.sfn_pipeline.arn

  definition = jsonencode({
    Comment = "Pipeline Bronze de ${var.fonte.nome}: descompactar -> Glue (aguardando término) e checagem de qualidade em paralelo."
    StartAt = "Descompactar"
    States = {
      Descompactar = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.unzip_dtcc.arn
          Payload = {
            Records = [
              {
                s3 = {
                  bucket = { "name.$" = "$.detail.bucket.name" }
                  object = { "key.$" = "$.detail.object.key" }
                }
              }
            ]
          }
        }
        ResultSelector = {
          "origem.$"       = "$.Payload.processados[0].origem"
          "destino.$"      = "$.Payload.processados[0].destino"
          "bytes.$"        = "$.Payload.processados[0].bytes"
          "execution_id.$" = "$.Payload.processados[0].execution_id"
        }
        Next = "EtapasParalelas"
      }
      EtapasParalelas = {
        Type = "Parallel"
        Branches = [
          {
            StartAt = "DispararGlue"
            States = {
              DispararGlue = {
                Type     = "Task"
                Resource = "arn:aws:states:::glue:startJobRun.sync"
                Parameters = {
                  JobName = aws_glue_job.bronze.name
                  Arguments = {
                    "--execution_id.$" = "$.execution_id"
                  }
                }
                Retry = [
                  {
                    ErrorEquals     = ["Glue.ConcurrentRunsExceededException"]
                    IntervalSeconds = 30
                    MaxAttempts     = 10
                    BackoffRate     = 1.5
                  }
                ]
                End = true
              }
            }
          },
          {
            StartAt = "ChecarQualidade"
            States = {
              ChecarQualidade = {
                Type     = "Task"
                Resource = "arn:aws:states:::lambda:invoke"
                Parameters = {
                  FunctionName = aws_lambda_function.quality_check.arn
                  Payload = {
                    detail = {
                      bucket = { "name.$" = "$$.Execution.Input.detail.bucket.name" }
                      object = { "key.$" = "$.destino" }
                    }
                  }
                }
                End = true
              }
            }
          }
        ]
        End = true
      }
    }
  })
}

output "state_machine_pipeline" {
  value = aws_sfn_state_machine.pipeline.arn
}
output "lambda_iniciar_pipeline" {
  value = aws_lambda_function.iniciar_pipeline.function_name
}
output "zip_arrived_rule_arn" {
  value = aws_cloudwatch_event_rule.zip_arrived.arn
}
