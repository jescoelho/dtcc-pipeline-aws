terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "prefix" {
  description = "Prefixo único para os buckets (ex.: seunome-dtcclab)"
  type        = string
}

variable "budget_email" {
  description = "E-mail que recebe o alerta de custo"
  type        = string
}

variable "budget_usd" {
  type    = number
  default = 10
}

data "aws_caller_identity" "me" {}

# ---------- Contrato de fonte ----------
# Única fonte de verdade sobre o que é específico da fonte DTCC (ver
# config/fontes/dtcc.yaml) -- caminhos S3 e regras de qualidade, lidos
# aqui e distribuídos pros recursos que já os consumiam antes como
# string cravada (Glue job, Lambdas, filtros do S3/EventBridge). Hoje só
# existe uma fonte, então "fonte" ainda não é uma variável de módulo --
# trocar de arquivo (ou adicionar uma segunda) é a próxima etapa da
# generalização.
locals {
  fonte = yamldecode(file("${path.module}/../config/fontes/dtcc.yaml"))
}

locals {
  bucket = "${var.prefix}-${data.aws_caller_identity.me.account_id}"
}

# ---------- S3 ----------
resource "aws_s3_bucket" "lake" {
  bucket        = local.bucket
  force_destroy = true # é laboratório: destroy apaga tudo
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

locals {
  glue_scripts = {
    bronze = "bronze_ingest.py"
  }
}

resource "aws_s3_object" "glue_script" {
  for_each = local.glue_scripts
  bucket   = aws_s3_bucket.lake.id
  key      = "scripts/${each.value}"
  source   = "${path.module}/../glue/${each.value}"
  etag     = filemd5("${path.module}/../glue/${each.value}")
}

# ---------- IAM do Glue ----------
data "aws_iam_policy_document" "glue_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["glue.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "glue" {
  name               = "${var.prefix}-glue"
  assume_role_policy = data.aws_iam_policy_document.glue_assume.json
}

resource "aws_iam_role_policy_attachment" "glue_service" {
  role       = aws_iam_role.glue.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

data "aws_iam_policy_document" "glue_s3" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/*"]
  }
  statement {
    # Protótipo de Glue Data Quality (ver glue/bronze_ingest.py) --
    # enableDataQualityCloudWatchMetrics publica pass/fail por regra
    # como métrica no namespace "Glue Data Quality". Não habilitamos
    # enableDataQualityResultsPublishing (repositório nativo do Glue),
    # então não precisa de permissão de Glue Data Quality API, só esta.
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "glue_s3" {
  role   = aws_iam_role.glue.id
  policy = data.aws_iam_policy_document.glue_s3.json
}

# ---------- Glue job — Bronze ----------
# Só a Bronze por enquanto. Silver (resolver a cadeia de eventos) e Gold
# entram como jobs novos quando chegar a vez de cada uma -- mesma lógica
# usada no b3-pipeline-aws: infraestrutura acompanha o que já foi
# validado localmente, não o roteiro inteiro de uma vez.
resource "aws_glue_job" "bronze" {
  name              = "${var.prefix}-bronze-ingest"
  role_arn          = aws_iam_role.glue.arn
  glue_version      = "4.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  timeout           = 30

  command {
    name            = "glueetl"
    script_location = "s3://${aws_s3_bucket.lake.id}/${aws_s3_object.glue_script["bronze"].key}"
    python_version  = "3"
  }

  default_arguments = {
    "--raw_path"            = "s3://${aws_s3_bucket.lake.id}/${local.fonte.raw_prefix}"
    "--bronze_path"         = "s3://${aws_s3_bucket.lake.id}/${local.fonte.bronze_prefix}"
    "--enable-metrics"      = "true"
    # Processa só os arquivos novos desde a última execução com sucesso,
    # em vez de reler raw/dtcc/ inteira a cada run -- ver glue/bronze_ingest.py
    # para a explicação completa.
    "--job-bookmark-option" = "job-bookmark-enable"

    # Campos do contrato de fonte (config/fontes/dtcc.yaml) que o job usa
    # pra montar o ruleset DQDL em runtime (ver _montar_ruleset em
    # glue/bronze_ingest.py), em vez de ter a string inteira cravada no
    # script -- listas viram string separada por vírgula porque
    # default_arguments do Glue só aceita string.
    "--colunas_obrigatorias" = join(",", local.fonte.colunas_obrigatorias)
    "--coluna_id"            = local.fonte.coluna_id
    "--unicidade_minima"     = tostring(local.fonte.unicidade_minima)
    "--coluna_dominio"       = local.fonte.coluna_dominio
    "--valores_dominio"      = join(",", local.fonte.valores_dominio)
  }
}

# ---------- Athena ----------
# Nome vem do contrato de fonte (local.fonte.nome), não mais cravado --
# achado da auditoria de generalização (04/10/2026): com nome=dtcc no
# YAML, o valor final é idêntico a antes, só deixa de ser hardcoded.
resource "aws_glue_catalog_database" "dtcc" {
  name = replace("${var.prefix}_${local.fonte.nome}", "-", "_")
}

resource "aws_athena_workgroup" "lab" {
  name          = "${var.prefix}-lab"
  force_destroy = true
  configuration {
    enforce_workgroup_configuration = true
    bytes_scanned_cutoff_per_query  = 10737418240 # 10 GB por query: protege contra query cara
    result_configuration {
      output_location = "s3://${aws_s3_bucket.lake.id}/athena-results/"
    }
  }
}

# ---------- Alerta de custo ----------
resource "aws_budgets_budget" "lab" {
  name         = "${var.prefix}-budget"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }
}

output "bucket" {
  value = aws_s3_bucket.lake.id
}
output "glue_job_bronze" {
  value = aws_glue_job.bronze.name
}
output "athena_workgroup" {
  value = aws_athena_workgroup.lab.name
}
output "database" {
  value = aws_glue_catalog_database.dtcc.name
}
