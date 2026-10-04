# Módulo "fonte" -- passo 2 da generalização (ver config/fontes/dtcc.yaml
# e docs/DECISOES.md, "Contrato de fonte"). Tudo que o passo 1 deixou
# cravado "um recurso por fonte, declarado à mão" (Glue job, as 6
# Lambdas, a state machine, as regras do EventBridge, a retenção de log)
# vira uma instância deste módulo -- uma por fonte, cada uma lendo seu
# próprio config/fontes/<nome>.yaml.
#
# O que continua FORA do módulo, compartilhado entre todas as fontes
# (ver ../main.tf): o bucket S3, o banco do Glue Data Catalog, o
# workgroup do Athena, o tópico SNS de alertas, a fila SQS de eventos
# falhos e o budget -- infraestrutura do laboratório, não de uma fonte
# específica. Passar esses recursos como variáveis (em vez de cada
# instância do módulo criar os seus) é o que evita, por exemplo, um
# banco do Glue Data Catalog por fonte quando o certo é um banco
# compartilhado com uma tabela por fonte.

variable "fonte" {
  description = <<-EOT
    O contrato de fonte inteiro, já decodificado (yamldecode de
    config/fontes/<nome>.yaml) -- este módulo lê os campos dele
    diretamente (var.fonte.raw_prefix, var.fonte.dias_historico, etc.),
    do mesmo jeito que o código pré-módulo lia local.fonte.*. Nenhum
    campo é repetido como variável própria -- um campo novo no YAML
    fica disponível aqui sem precisar editar variables.tf.
  EOT
  type        = any
}

variable "prefix" {
  description = "Prefixo do laboratório (compartilhado entre todas as fontes, ex.: seunome-dtcclab)"
  type        = string
}

variable "region" {
  type = string
}

variable "account_id" {
  description = "Conta AWS (data.aws_caller_identity.me.account_id no root) -- usado pra montar ARNs do Glue Data Catalog que o Athena exige (catalog e table wildcard)."
  type        = string
}

variable "bucket_name" {
  type = string
}

variable "bucket_arn" {
  type = string
}

variable "glue_role_arn" {
  description = "Role do Glue, compartilhada entre fontes -- permissões já são bucket inteiro (ver ../main.tf), não precisa de uma role por fonte."
  type        = string
}

variable "glue_catalog_database_name" {
  type = string
}

variable "glue_catalog_database_arn" {
  type = string
}

variable "athena_workgroup_name" {
  type = string
}

variable "athena_workgroup_arn" {
  type = string
}

variable "sns_topic_arn" {
  description = "Tópico SNS de alertas, compartilhado -- um só canal pra todas as fontes, não importa qual gerou o alerta (o assunto do e-mail já identifica, via NOME_FONTE)."
  type        = string
}

variable "dlq_arn" {
  description = "Fila SQS compartilhada, destino de dead_letter_config das regras do EventBridge desta fonte."
  type        = string
}

variable "glue_script_source_path" {
  description = "Caminho local do script Python do Glue job Bronze desta fonte (hoje sempre glue/bronze_ingest.py -- script genérico, dirigido pelos argumentos do contrato; um script por fonte só seria necessário se uma fonte futura precisar de lógica de parsing diferente)."
  type        = string
}
