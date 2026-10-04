# Orquestração do pipeline Bronze via Step Functions -- substitui o
# encadeamento anterior de Lambdas + regras do EventBridge (unzip_dtcc
# disparado direto pelo S3 -> trigger_bronze e quality_check como alvos
# independentes da mesma regra do EventBridge).
#
# Motivação real (ver README, seção Step Functions): em produção,
# 03/10/2026, o mesmo arquivo gerou duas execuções completas do pipeline
# -- não concorrentes (o MaxConcurrentRuns padrão do Glue já é 1), mas
# sequenciais: a notificação do S3 chegou duplicada (comportamento
# "at-least-once" documentado pela AWS), e cada entrega disparava o
# fluxo inteiro de novo, do zero, sem nenhuma noção de "isso já rodou".
#
# A state machine por si só não resolve isso -- um alvo nativo de regra
# do EventBridge não permite controlar o nome da execução a partir do
# evento (só o input), e sem controlar o nome toda entrega duplicada
# ainda geraria uma execução nova. Por isso existe lambda/iniciar_pipeline.py:
# decide um nome de execução determinístico (a partir do nome do
# arquivo) antes de chamar StartExecution -- a idempotência real vem da
# própria API do Step Functions a partir daí (mesmo nome + mesmo input
# = sucesso idêntico, sem nova execução; mesmo nome + input diferente =
# ExecutionAlreadyExists, tratado como sucesso por aquela Lambda).

# ---------- Lambda que inicia a execução ----------

data "archive_file" "iniciar_pipeline_lambda" {
  type        = "zip"
  source_file = "${path.module}/../lambda/iniciar_pipeline.py"
  output_path = "${path.module}/.build/iniciar_pipeline.zip"
}

resource "aws_iam_role" "iniciar_pipeline_lambda" {
  name               = "${var.prefix}-iniciar-pipeline-lambda"
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
  function_name    = "${var.prefix}-iniciar-pipeline"
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

# ---------- Gatilho: .zip chegou ----------
# Substitui o `lambda_function` direto que existia em
# aws_s3_bucket_notification.unzip_on_upload (lambda.tf) antes da state
# machine -- agora o evento passa pelo EventBridge (já habilitado ali
# com `eventbridge = true`) pra esta Lambda decidir o nome da execução
# antes de iniciar o pipeline.
resource "aws_cloudwatch_event_rule" "zip_arrived" {
  name        = "${var.prefix}-zip-arrived"
  description = "Zip do Cumulative chegou -- inicia a state machine do pipeline"

  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [aws_s3_bucket.lake.id] }
      object = {
        key = [
          { prefix = local.fonte.zip_prefix },
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
    arn = aws_sqs_queue.eventos_falhos.arn
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
  name               = "${var.prefix}-sfn-pipeline"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
}

data "aws_iam_policy_document" "sfn_pipeline_permissions" {
  statement {
    # As duas Lambdas que a state machine invoca como Task -- permissão
    # baseada em identidade (papel da state machine), não em política de
    # recurso das Lambdas (ver nota em quality_check.tf).
    actions = ["lambda:InvokeFunction"]
    resources = [
      aws_lambda_function.unzip_dtcc.arn,
      aws_lambda_function.quality_check.arn,
    ]
  }
  statement {
    # Ações exigidas pelo padrão .sync do integration glue:startJobRun,
    # confirmadas contra a documentação oficial da AWS (Step Functions
    # precisa poder consultar o andamento do job pra "esperar" ele
    # terminar, e também poder pará-lo). O Glue não suporta política no
    # nível de recurso pra estas ações -- Resource tem que ser "*".
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

# Definição em ASL (Amazon States Language). Comentários de design:
#
# - "Descompactar": invoca unzip_dtcc.py sem mudar o formato de evento
#   que ele já espera ({"Records": [...]}, o formato nativo de
#   notificação do S3) -- a state machine monta esse formato a partir
#   do bucket/key recebidos no input (o evento original do EventBridge,
#   repassado pela Lambda iniciar_pipeline).
# - "EtapasParalelas": Glue e checagem de qualidade rodam ao mesmo
#   tempo, exatamente como no desenho anterior (dois alvos
#   independentes da mesma regra do EventBridge) -- só a orquestração
#   mudou, não o paralelismo.
#   - Branch do Glue usa `glue:startJobRun.sync`: a state machine fica
#     esperando o job terminar, em vez de só disparar e seguir (como o
#     trigger_bronze.py fazia). O desfecho (sucesso/falha) continua
#     sendo gravado na tabela de controle por job_concluido.py, que
#     escuta o evento nativo "Glue Job State Change" do EventBridge --
#     esse mecanismo não mudou, continua funcionando igual
#     independente de quem chamou StartJobRun.
#   - Retry em Glue.ConcurrentRunsExceededException: defensivo contra
#     duas execuções DIFERENTES (arquivos diferentes, não o mesmo
#     evento duplicado) chegando perto o bastante pra colidir no limite
#     de concorrência do Glue (MaxConcurrentRuns=1, o padrão da API) --
#     em vez de a execução falhar, ela espera e tenta de novo.
#   - Branch da qualidade usa `$$.Execution.Input` (o input ORIGINAL da
#     execução, não a saída de "Descompactar") pra pegar o nome do
#     bucket -- unzip_dtcc.py não devolve o bucket na resposta, só
#     origem/destino/bytes/execution_id.
resource "aws_sfn_state_machine" "pipeline" {
  name     = "${var.prefix}-pipeline"
  role_arn = aws_iam_role.sfn_pipeline.arn

  definition = jsonencode({
    Comment = "Pipeline Bronze do DTCC: descompactar -> Glue (aguardando término) e checagem de qualidade em paralelo."
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
