#!/usr/bin/env bash
# Cria/atualiza as tabelas do Athena (dtcc_bronze, controle_execucoes) e
# roda o MSCK REPAIR de cada uma, via API do Athena -- sem precisar
# colar SQL no console manualmente.
#
# Parametrizado igual ao ingerir_cumulative.sh: lê BUCKET, DATABASE e
# WORKGROUP do Terraform por padrão, mas aceita override por variável de
# ambiente (útil se você renomear o prefixo ou mudar de conta).
#
# Idempotente: CREATE EXTERNAL TABLE usa IF NOT EXISTS, e MSCK REPAIR só
# adiciona partições novas -- seguro rodar de novo a qualquer momento
# (por exemplo, depois de cada ingestão nova, pra enxergar a partição do
# dia no Athena).
#
# O SQL aqui é uma cópia do que está em athena/queries.sql -- se mudar o
# schema de uma tabela, atualize os dois (limite assumido: sem um único
# lugar de verdade pro SQL nesta etapa).
#
# Uso:
#   ./scripts/configurar_athena.sh
#
# Variáveis de ambiente opcionais: BUCKET, DATABASE, WORKGROUP (senão lê
# do terraform/)

set -euo pipefail

if [ -z "${BUCKET:-}" ] || [ -z "${DATABASE:-}" ] || [ -z "${WORKGROUP:-}" ]; then
  TERRAFORM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../terraform" && pwd)"
  BUCKET="${BUCKET:-$(terraform -chdir="$TERRAFORM_DIR" output -raw bucket)}"
  DATABASE="${DATABASE:-$(terraform -chdir="$TERRAFORM_DIR" output -raw database)}"
  WORKGROUP="${WORKGROUP:-$(terraform -chdir="$TERRAFORM_DIR" output -raw athena_workgroup)}"
fi

echo "Bucket:    $BUCKET"
echo "Database:  $DATABASE"
echo "Workgroup: $WORKGROUP"
echo

executar() {
  local descricao="$1"
  local sql="$2"
  echo "-> $descricao"

  local query_id
  query_id=$(aws athena start-query-execution \
    --query-string "$sql" \
    --query-execution-context "Database=$DATABASE" \
    --work-group "$WORKGROUP" \
    --query 'QueryExecutionId' --output text)

  while true; do
    local estado
    estado=$(aws athena get-query-execution --query-execution-id "$query_id" \
      --query 'QueryExecution.Status.State' --output text)
    case "$estado" in
      SUCCEEDED)
        echo "   OK"
        break
        ;;
      FAILED|CANCELLED)
        local motivo
        motivo=$(aws athena get-query-execution --query-execution-id "$query_id" \
          --query 'QueryExecution.Status.StateChangeReason' --output text)
        echo "   FALHOU: $motivo" >&2
        return 1
        ;;
      *)
        sleep 2
        ;;
    esac
  done
}

sql_dtcc_bronze="CREATE EXTERNAL TABLE IF NOT EXISTS ${DATABASE}.dtcc_bronze (
  \`Dissemination Identifier\` string,
  \`Original Dissemination Identifier\` string,
  \`Action type\` string,
  \`Event type\` string,
  \`Event timestamp\` string,
  \`Asset Class\` string,
  \`Product name\` string,
  \`Notional amount-Leg 1\` string,
  \`Notional currency-Leg 1\` string,
  line_number bigint,
  ingerido_em timestamp
)
PARTITIONED BY (arquivo_origem string)
STORED AS PARQUET
LOCATION 's3://${BUCKET}/bronze/dtcc/'"

sql_controle_execucoes="CREATE EXTERNAL TABLE IF NOT EXISTS ${DATABASE}.controle_execucoes (
  \`timestamp\` string,
  origem string,
  csv string,
  zip string,
  job_run_id string,
  execution_id string,
  linhas int,
  problemas array<string>,
  status string,
  duracao_segundos int,
  regra string,
  outcome string,
  motivo_falha string,
  metrica_nome string,
  metrica_valor double
)
PARTITIONED BY (dt string)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
LOCATION 's3://${BUCKET}/logs/execucoes/'"

executar "Criando/confirmando tabela dtcc_bronze" "$sql_dtcc_bronze"
executar "Atualizando partições de dtcc_bronze (MSCK REPAIR)" "MSCK REPAIR TABLE ${DATABASE}.dtcc_bronze"

# DROP (só metadado -- não toca nos dados em s3://.../logs/execucoes/) em
# vez de "CREATE IF NOT EXISTS": o schema de controle_execucoes mudou
# (novas colunas zip, execution_id, duracao_segundos) e CREATE EXTERNAL
# TABLE IF NOT EXISTS não atualiza o schema de uma tabela que já existe
# -- rodar o script de novo sem o DROP deixaria as colunas novas de fora,
# silenciosamente.
executar "Recriando tabela controle_execucoes (schema pode ter mudado)" \
  "DROP TABLE IF EXISTS ${DATABASE}.controle_execucoes"
executar "Criando/confirmando tabela controle_execucoes" "$sql_controle_execucoes"
executar "Atualizando partições de controle_execucoes (MSCK REPAIR)" "MSCK REPAIR TABLE ${DATABASE}.controle_execucoes"

echo
echo "Pronto. As duas tabelas estão criadas/atualizadas no Athena."
