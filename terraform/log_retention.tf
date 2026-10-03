# Retenção de log das três Lambdas.
#
# Sem isso, o log group de cada Lambda é criado automaticamente no
# primeiro invoke com retenção "nunca expira" -- custo de armazenamento
# crescendo pra sempre, sem necessidade num laboratório que não precisa
# de histórico de meses pra depurar nada.
#
# Os log groups JÁ EXISTEM (foram criados pelas invocações que já
# rodamos), então estes recursos precisam ser importados pro estado do
# Terraform antes do apply -- senão ele tenta criar um log group que já
# existe e falha. Ver README.md, seção desta etapa, pelos comandos de
# import.

resource "aws_cloudwatch_log_group" "unzip_dtcc" {
  name              = "/aws/lambda/${aws_lambda_function.unzip_dtcc.function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "trigger_bronze" {
  name              = "/aws/lambda/${aws_lambda_function.trigger_bronze.function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "quality_check" {
  name              = "/aws/lambda/${aws_lambda_function.quality_check.function_name}"
  retention_in_days = 14
}
