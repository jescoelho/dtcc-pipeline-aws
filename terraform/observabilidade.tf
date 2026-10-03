# Observabilidade, etapa 1: avisar quando o job Glue Bronze falhar.
#
# Caminho 100% nativo da AWS, sem Lambda nova: o próprio Glue emite um
# evento de mudança de estado no EventBridge; uma regra filtra esse evento
# para FAILED/TIMEOUT/ERROR e publica num tópico SNS, que manda e-mail.
# Reaproveita var.budget_email (já usado pelo alerta de custo) -- não é
# o mesmo mecanismo (budget é custo, isto é execução), mas é o mesmo
# destinatário: você.
#
# Escopo desta etapa: só falha. "Sucesso" não é incluído de propósito --
# um e-mail por execução bem-sucedida, todo dia, é ruído que ensina a
# ignorar o canal. Falha é o caso em que alguém realmente precisa olhar.
#
# A checagem de qualidade de dados ANTES do Glue rodar (ver README,
# "Tarefas futuras") continua pendente -- isto aqui cobre só "o job
# rodou e explodiu", não "o dado que chegou está estranho".

resource "aws_sns_topic" "alertas" {
  name = "${var.prefix}-alertas"
}

data "aws_iam_policy_document" "alertas_topic_policy" {
  statement {
    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.alertas.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_sns_topic_policy" "alertas" {
  arn    = aws_sns_topic.alertas.arn
  policy = data.aws_iam_policy_document.alertas_topic_policy.json
}

resource "aws_sns_topic_subscription" "alertas_email" {
  topic_arn = aws_sns_topic.alertas.arn
  protocol  = "email"
  endpoint  = var.budget_email
}

resource "aws_cloudwatch_event_rule" "glue_bronze_failed" {
  name        = "${var.prefix}-glue-bronze-failed"
  description = "Job Glue Bronze terminou em FAILED, TIMEOUT ou ERROR"

  event_pattern = jsonencode({
    source      = ["aws.glue"]
    "detail-type" = ["Glue Job State Change"]
    detail = {
      jobName = [aws_glue_job.bronze.name]
      state   = ["FAILED", "TIMEOUT", "ERROR"]
    }
  })
}

resource "aws_cloudwatch_event_target" "glue_bronze_failed_to_sns" {
  rule      = aws_cloudwatch_event_rule.glue_bronze_failed.name
  target_id = "sns-alertas"
  arn       = aws_sns_topic.alertas.arn
}

output "sns_alertas" {
  value = aws_sns_topic.alertas.arn
}
