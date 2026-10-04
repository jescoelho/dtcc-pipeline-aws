# Tópico SNS de alertas -- compartilhado entre todas as fontes (ver
# module "dtcc" em main.tf): um só canal, não importa qual fonte ou
# qual causa gerou o alerta (o assunto do e-mail já identifica a fonte,
# via NOME_FONTE em cada Lambda). As regras do EventBridge que publicam
# aqui (falha do Glue, checagem de atualidade/linhagem, etc.) são por
# fonte e vivem dentro do módulo (modules/fonte/observabilidade.tf,
# atualidade_linhagem.tf, ingestao_agendada.tf).

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

output "sns_alertas" {
  value = aws_sns_topic.alertas.arn
}
