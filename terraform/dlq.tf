# Dead-letter queue compartilhada por todas as fontes, pelos alvos de
# regras do EventBridge que têm dead_letter_config (dentro do módulo
# "fonte": zip_arrived -> iniciar_pipeline, glue_bronze_concluido ->
# job_concluido, checar_pipeline_agenda -> checar_pipeline,
# ingestao_agendada -> ingerir_cumulative).
#
# Sem isso, uma falha de entrega (throttle da Lambda, erro transiente da
# AWS, um bug novo no código) faz o evento desaparecer silenciosamente.
#
# Uma fila só, compartilhada -- a mensagem que cai aqui já carrega o
# evento original, então não há necessidade de uma fila por fonte nesta
# escala. A política abaixo lista as regras de TODAS as fontes
# instanciadas (hoje só module.dtcc) -- adicionar uma segunda fonte
# significa acrescentar seus 4 ARNs de regra aqui também; não é
# automático ainda (isso seria o passo 3 da generalização, com um
# for_each sobre as fontes, deixado pra quando existir uma segunda de
# verdade).

resource "aws_sqs_queue" "eventos_falhos" {
  name                      = "${var.prefix}-eventos-falhos"
  message_retention_seconds = 1209600 # 14 dias (máximo do SQS)
}

data "aws_iam_policy_document" "eventos_falhos_policy" {
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.eventos_falhos.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values = [
        module.dtcc.zip_arrived_rule_arn,
        module.dtcc.glue_bronze_concluido_rule_arn,
        module.dtcc.checar_pipeline_agenda_rule_arn,
        module.dtcc.ingestao_agendada_rule_arn,
      ]
    }
  }
}

resource "aws_sqs_queue_policy" "eventos_falhos" {
  queue_url = aws_sqs_queue.eventos_falhos.id
  policy    = data.aws_iam_policy_document.eventos_falhos_policy.json
}

output "dlq_eventos_falhos" {
  value = aws_sqs_queue.eventos_falhos.id
}
