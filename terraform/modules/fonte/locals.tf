locals {
  # Raiz de nomenclatura de todo recurso desta instância do módulo --
  # inclui var.fonte.nome pra uma segunda fonte (segunda instância do
  # módulo) nunca colidir com esta (duas Lambdas "unzip", duas regras
  # "zip-arrived", etc., no mesmo laboratório). Convenção única usada em
  # todo o módulo: "${local.nome}-<recurso>".
  nome = "${var.prefix}-${var.fonte.nome}"
}

# Assume role de Lambda -- idêntico pra todas as Lambdas desta fonte
# (e de qualquer outra), redeclarado aqui em vez de recebido como
# variável: um data source não tem estado, recalculá-lo em cada
# instância do módulo não duplica recurso nenhum na AWS.
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}
