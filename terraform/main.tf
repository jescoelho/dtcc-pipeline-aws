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
# config/fontes/dtcc.yaml) -- lido aqui e passado pro módulo "fonte"
# (ver module "dtcc" abaixo), que materializa Glue job, Lambdas, state
# machine e regras do EventBridge pra essa fonte. Uma segunda fonte
# real (passo 3 da generalização, ver docs/DECISOES.md) seria outro
# yamldecode + outra instância do módulo, sem tocar no módulo em si.
locals {
  fonte = yamldecode(file("${path.module}/../config/fontes/dtcc.yaml"))
}

locals {
  bucket = "${var.prefix}-${data.aws_caller_identity.me.account_id}"
}

# ---------- S3 ----------
# Compartilhado entre todas as fontes -- um bucket só para o
# laboratório, cada fonte com seus próprios prefixos (contrato de
# fonte: zip_prefix/raw_prefix/bronze_prefix).
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

# Só manda os eventos de criação de objeto pro EventBridge -- não invoca
# nenhuma Lambda diretamente. Um bucket só aceita UMA
# aws_s3_bucket_notification, por isso fica aqui (compartilhado), não
# dentro do módulo: cada fonte filtra esse mesmo fluxo de eventos pela
# sua própria regra do EventBridge (zip_arrived, dentro do módulo),
# usando seu zip_prefix.
resource "aws_s3_bucket_notification" "unzip_on_upload" {
  bucket      = aws_s3_bucket.lake.id
  eventbridge = true
}

# ---------- IAM do Glue ----------
# Compartilhada entre fontes -- permissões já cobrem o bucket inteiro
# (s3:*Object/ListBucket), não há necessidade de uma role por fonte.
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
    # como métrica no namespace "Glue Data Quality".
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "glue_s3" {
  role   = aws_iam_role.glue.id
  policy = data.aws_iam_policy_document.glue_s3.json
}

# ---------- Athena / Glue Data Catalog ----------
# Compartilhados entre fontes -- UM banco pro laboratório inteiro, com
# uma tabela Bronze por fonte dentro dele (dtcc_bronze hoje; uma
# segunda fonte ganharia <fonte>_bronze no mesmo banco), mais
# controle_execucoes, compartilhada. Até a extensão deste módulo
# (04/10/2026), o nome do banco vinha de local.fonte.nome -- um banco
# por fonte, em vez de um banco por laboratório, o que teria criado um
# segundo banco (e seria preciso migrar as tabelas) na primeira vez que
# uma segunda fonte existisse. Corrigido aqui: nome vem só de
# var.prefix, antes de existir uma segunda fonte de verdade pra sentir
# essa dor.
resource "aws_glue_catalog_database" "lab" {
  name = replace(var.prefix, "-", "_")
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

# ---------- Fonte: DTCC ----------
# Uma instância do módulo "fonte" por fonte real -- hoje só esta.
# Adicionar uma segunda fonte significa um config/fontes/<nome>.yaml
# novo + outro bloco `module` igual a este, apontando pro YAML dela;
# nada dentro do módulo precisa mudar (ver docs/DECISOES.md).
module "dtcc" {
  source = "./modules/fonte"

  fonte      = local.fonte
  prefix     = var.prefix
  region     = var.region
  account_id = data.aws_caller_identity.me.account_id

  bucket_name = aws_s3_bucket.lake.id
  bucket_arn  = aws_s3_bucket.lake.arn

  glue_role_arn = aws_iam_role.glue.arn

  glue_catalog_database_name = aws_glue_catalog_database.lab.name
  glue_catalog_database_arn  = aws_glue_catalog_database.lab.arn

  athena_workgroup_name = aws_athena_workgroup.lab.name
  athena_workgroup_arn  = aws_athena_workgroup.lab.arn

  sns_topic_arn = aws_sns_topic.alertas.arn
  dlq_arn       = aws_sqs_queue.eventos_falhos.arn

  glue_script_source_path = "${path.module}/../glue/bronze_ingest.py"
}

# ---------- Saídas ----------
# Nomes mantidos idênticos aos de antes do módulo -- scripts/*.sh e o
# README continuam usando `terraform output -raw bucket` etc. sem
# precisar saber que o valor agora vem de dentro de um módulo.
output "bucket" {
  value = aws_s3_bucket.lake.id
}
output "database" {
  value = aws_glue_catalog_database.lab.name
}
output "athena_workgroup" {
  value = aws_athena_workgroup.lab.name
}
output "glue_job_bronze" {
  value = module.dtcc.glue_job_bronze
}
output "lambda_unzip" {
  value = module.dtcc.lambda_unzip
}
output "lambda_quality_check" {
  value = module.dtcc.lambda_quality_check
}
output "lambda_job_concluido" {
  value = module.dtcc.lambda_job_concluido
}
output "lambda_iniciar_pipeline" {
  value = module.dtcc.lambda_iniciar_pipeline
}
output "lambda_checar_pipeline" {
  value = module.dtcc.lambda_checar_pipeline
}
output "lambda_ingerir_cumulative" {
  value = module.dtcc.lambda_ingerir_cumulative
}
output "state_machine_pipeline" {
  value = module.dtcc.state_machine_pipeline
}
