# Glue job Bronze desta fonte. A role (var.glue_role_arn) e o banco do
# Glue Data Catalog (var.glue_catalog_database_name) são compartilhados
# entre fontes (ver ../main.tf) -- só o job em si, e o script que ele
# roda, são por fonte.

resource "aws_s3_object" "glue_script" {
  bucket = var.bucket_name
  # Namespaced por fonte (scripts/<fonte>/...) -- script de uma fonte
  # nunca sobrescreve o de outra, mesmo que os dois se chamem
  # "bronze_ingest.py" no disco local.
  key    = "scripts/${var.fonte.nome}/${basename(var.glue_script_source_path)}"
  source = var.glue_script_source_path
  etag   = filemd5(var.glue_script_source_path)
}

resource "aws_glue_job" "bronze" {
  name              = "${local.nome}-bronze-ingest"
  role_arn          = var.glue_role_arn
  glue_version      = "4.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  timeout           = 30

  command {
    name            = "glueetl"
    script_location = "s3://${var.bucket_name}/${aws_s3_object.glue_script.key}"
    python_version  = "3"
  }

  default_arguments = {
    "--raw_path"       = "s3://${var.bucket_name}/${var.fonte.raw_prefix}"
    "--bronze_path"    = "s3://${var.bucket_name}/${var.fonte.bronze_prefix}"
    "--enable-metrics" = "true"
    # Processa só os arquivos novos desde a última execução com sucesso
    # -- ver glue/bronze_ingest.py pra explicação completa.
    "--job-bookmark-option" = "job-bookmark-enable"

    # Campos do contrato de fonte que o job usa pra montar o ruleset
    # DQDL em runtime (_montar_ruleset em glue/bronze_ingest.py) --
    # listas viram string separada por vírgula porque default_arguments
    # do Glue só aceita string.
    "--colunas_obrigatorias" = join(",", var.fonte.colunas_obrigatorias)
    "--coluna_id"            = var.fonte.coluna_id
    "--unicidade_minima"     = tostring(var.fonte.unicidade_minima)
    "--coluna_dominio"       = var.fonte.coluna_dominio
    "--valores_dominio"      = join(",", var.fonte.valores_dominio)
  }
}
