# Dead-letter queue compartilhada pelos alvos de regras do EventBridge
# que têm dead_letter_config: zip_arrived -> iniciar_pipeline (ver
# step_functions.tf) e glue_bronze_concluido -> job_concluido (ver
# observabilidade.tf).
#
# Sem isso, uma falha de entrega (throttle da Lambda, erro transiente da
# AWS, um bug novo no código) faz o evento desaparecer silenciosamente --
# sem log, sem alerta, sem nada. É um ponto cego exatamente na entrega do
# evento que alimenta a observabilidade (observabilidade.tf), então faz
# sentido fechar antes de seguir pra Silver.
#
# Uma fila só, compartilhada pelos alvos -- a mensagem que cai aqui já
# carrega o evento original e qual regra/alvo falhou, então não há
# necessidade de uma fila por Lambda nesta escala.

resource "aws_sqs_queue" "eventos_falhos" {
  name                      = "${var.prefix}-eventos-falhos"
  message_retention_seconds = 1209600 # 14 dias (máximo do SQS) -- tempo de sobra pra investigar
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
      # Lista as duas regras que usam esta fila como DLQ -- faltava a
      # glue_bronze_concluido aqui antes (só csv_arrived estava
      # permitida), gap pré-existente que ficou visível ao reescrever
      # este arquivo pra remover a regra aposentada.
      values = [
        aws_cloudwatch_event_rule.zip_arrived.arn,
        aws_cloudwatch_event_rule.glue_bronze_concluido.arn,
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
