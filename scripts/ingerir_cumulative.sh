#!/usr/bin/env bash
# Copia o relatório Cumulative do DTCC PPD direto do bucket S3 público do
# DTCC para o nosso bucket -- SEM passar pelo computador local. Confirmado
# manualmente em 03/10/2026: `aws s3 cp` entre dois URIs s3:// usa a API
# CopyObject, que roda inteiramente dentro da AWS (servidor-a-servidor),
# em vez de baixar e depois subir pelo processo local.
#
# Por enquanto este script PARA AQUI -- sobe o .zip ainda compactado para
# raw/dtcc_zip/. Descompactar e disparar o job Glue Bronze ainda não estão
# automatizados; são o próximo passo.
#
# O padrão de URL (nome do bucket, caminho, nome do arquivo) NÃO é
# documentado oficialmente pelo DTCC -- achado em repositórios de
# terceiros, confirmado manualmente contra o dashboard oficial. Pode
# mudar sem aviso.
#
# Uso:
#   ./scripts/ingerir_cumulative.sh [DATA] [CLASSE_ATIVO] [FONTE]
#   DATA          formato AAAA-MM-DD, default: hoje
#   CLASSE_ATIVO  RATES | CREDITS | EQUITIES | FOREX | COMMODITIES, default: RATES
#   FONTE         cftc | sec | ca, default: cftc
#
# Variável de ambiente opcional: BUCKET (senão lê do terraform/)

set -euo pipefail

DATA="${1:-$(date +%F)}"
CLASSE_ATIVO="${2:-RATES}"
FONTE="${3:-cftc}"
DATA_UNDERSCORE="${DATA//-/_}"
FONTE_UPPER=$(echo "$FONTE" | tr '[:lower:]' '[:upper:]')

if [ -z "${BUCKET:-}" ]; then
  TERRAFORM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../terraform" && pwd)"
  BUCKET=$(terraform -chdir="$TERRAFORM_DIR" output -raw bucket)
fi

NOME_ARQUIVO="${FONTE_UPPER}_CUMULATIVE_${CLASSE_ATIVO}_${DATA_UNDERSCORE}.zip"
ORIGEM="s3://kgc0418-tdw-data-0/${FONTE}/eod/${NOME_ARQUIVO}"
DESTINO="s3://${BUCKET}/raw/dtcc_zip/${NOME_ARQUIVO}"

echo "Copiando (servidor-a-servidor, sem passar por aqui):"
echo "  de:   $ORIGEM"
echo "  para: $DESTINO"
aws s3 cp "$ORIGEM" "$DESTINO"
echo "Concluído."
