#!/usr/bin/env bash
# Copia o relatório Cumulative do DTCC PPD direto do bucket S3 público do
# DTCC para o nosso bucket -- SEM passar pelo computador local. Confirmado
# manualmente em 03/10/2026: `aws s3 cp` entre dois URIs s3:// usa a API
# CopyObject, que roda inteiramente dentro da AWS (servidor-a-servidor),
# em vez de baixar e depois subir pelo processo local.
#
# Por enquanto este script PARA AQUI -- sobe o .zip ainda compactado para
# <zip_prefix> (contrato de fonte). Descompactar e disparar o job Glue
# Bronze ainda não estão automatizados; são o próximo passo.
#
# O padrão de URL (nome do bucket, caminho, nome do arquivo) NÃO é
# documentado oficialmente pelo DTCC -- achado em repositórios de
# terceiros, confirmado manualmente contra o dashboard oficial. Pode
# mudar sem aviso. Isolado em config/fontes/dtcc.yaml (origem_bucket,
# origem_padrao) em vez de cravado aqui -- achado da auditoria de
# generalização (04/10/2026): até esta extensão, nenhum campo deste
# script vinha do contrato de fonte, só os outros consumidores (Terraform,
# Lambdas) liam o YAML.
#
# Uso:
#   ./scripts/ingerir_cumulative.sh [DATA] [CLASSE_ATIVO] [FONTE]
#   DATA          formato AAAA-MM-DD, default: hoje
#   CLASSE_ATIVO  RATES | CREDITS | EQUITIES | FOREX | COMMODITIES, default: vem do contrato
#   FONTE         cftc | sec | ca, default: vem do contrato
#
# Variáveis de ambiente opcionais: BUCKET, CONTRATO_FONTE (caminho do YAML,
# default config/fontes/dtcc.yaml -- senão BUCKET lê do terraform/)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTRATO_FONTE="${CONTRATO_FONTE:-$SCRIPT_DIR/../config/fontes/dtcc.yaml}"

ler_campo_yaml() {
  python3 -c "
import yaml, sys
with open(sys.argv[1]) as f:
    contrato = yaml.safe_load(f)
print(contrato[sys.argv[2]])
" "$CONTRATO_FONTE" "$1"
}

ORIGEM_BUCKET=$(ler_campo_yaml origem_bucket)
ORIGEM_PADRAO=$(ler_campo_yaml origem_padrao)
ZIP_PREFIX=$(ler_campo_yaml zip_prefix)
CLASSE_ATIVO_DEFAULT=$(ler_campo_yaml origem_classe_ativo_default)
FONTE_DEFAULT=$(ler_campo_yaml origem_fonte_default)

DATA="${1:-$(date +%F)}"
CLASSE_ATIVO="${2:-$CLASSE_ATIVO_DEFAULT}"
FONTE="${3:-$FONTE_DEFAULT}"
DATA_UNDERSCORE="${DATA//-/_}"
FONTE_UPPER=$(echo "$FONTE" | tr '[:lower:]' '[:upper:]')

if [ -z "${BUCKET:-}" ]; then
  TERRAFORM_DIR="$(cd "$SCRIPT_DIR/../terraform" && pwd)"
  BUCKET=$(terraform -chdir="$TERRAFORM_DIR" output -raw bucket)
fi

NOME_ARQUIVO=$(python3 -c "
import sys
print(sys.argv[1].format(
    fonte=sys.argv[2], fonte_upper=sys.argv[3],
    classe_ativo=sys.argv[4], data_underscore=sys.argv[5],
))
" "$ORIGEM_PADRAO" "$FONTE" "$FONTE_UPPER" "$CLASSE_ATIVO" "$DATA_UNDERSCORE")
ORIGEM="s3://${ORIGEM_BUCKET}/${NOME_ARQUIVO}"
DESTINO="s3://${BUCKET}/${ZIP_PREFIX}$(basename "$NOME_ARQUIVO")"

echo "Copiando (servidor-a-servidor, sem passar por aqui):"
echo "  de:   $ORIGEM"
echo "  para: $DESTINO"
aws s3 cp "$ORIGEM" "$DESTINO"
echo "Concluído."
