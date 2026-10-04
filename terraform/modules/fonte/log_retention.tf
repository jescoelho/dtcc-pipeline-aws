# Retenção de log das Lambdas desta fonte -- sem isso, o log group de
# cada Lambda é criado automaticamente no primeiro invoke com retenção
# "nunca expira" (ver docs/DECISOES.md, "Boas práticas, etapa 2").
#
# Diferente da versão pré-módulo, nenhum destes log groups precisa de
# `terraform import`: nomes novos (${local.nome}-...), nunca invocados
# manualmente antes de o Terraform existir -- o próprio Terraform cria
# cada log group junto com sua Lambda.

resource "aws_cloudwatch_log_group" "unzip_dtcc" {
  name              = "/aws/lambda/${aws_lambda_function.unzip_dtcc.function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "iniciar_pipeline" {
  name              = "/aws/lambda/${aws_lambda_function.iniciar_pipeline.function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "quality_check" {
  name              = "/aws/lambda/${aws_lambda_function.quality_check.function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "job_concluido" {
  name              = "/aws/lambda/${aws_lambda_function.job_concluido.function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "checar_pipeline" {
  name              = "/aws/lambda/${aws_lambda_function.checar_pipeline.function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "ingerir_cumulative" {
  name              = "/aws/lambda/${aws_lambda_function.ingerir_cumulative.function_name}"
  retention_in_days = 14
}
