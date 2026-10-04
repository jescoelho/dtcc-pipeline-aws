"""Lambda que busca o Cumulative diário do DTCC PPD, sem depender do
computador local estar ligado.

Reimplementação, como Lambda agendada, de scripts/ingerir_cumulative.sh
(ver docs/DECISOES.md) -- mesma cópia servidor-a-servidor (API
CopyObject do S3, via boto3 em vez de `aws s3 cp`, mas o mecanismo é
idêntico: o arquivo nunca passa por fora da AWS). Disparada uma vez por
dia útil por aws_cloudwatch_event_rule.ingestao_agendada (ver
terraform/ingestao_agendada.tf) -- elimina o único passo manual que
restava em todo o pipeline.

Escopo: só a cópia do .zip pro bucket do laboratório (ZIP_PREFIX). A
partir daí o fluxo já é automático (evento do S3 -> Step Functions, ver
docs/DECISOES.md) -- esta Lambda não muda nada depois do CopyObject.

O nome do arquivo de origem segue o padrão do contrato de fonte
(ORIGEM_PADRAO, vindo de config/fontes/dtcc.yaml), com a data de hoje
(UTC) por padrão -- mesmo padrão do script manual, que usa `date +%F`
(hora local de quem roda). `event.get("data")`/`classe_ativo`/`fonte`
permitem sobrepor isso num invoke manual (reprocessar um dia específico
ou outra classe de ativo), mesmo uso dos argumentos posicionais do
script.

Se a cópia falhar (ex.: o DTCC ainda não publicou o arquivo de hoje no
horário agendado, ou mudou o padrão de nome), publica um alerta no mesmo
tópico SNS do resto do pipeline antes de propagar a exceção -- diferente
das outras Lambdas que reagem a eventos nativos da AWS (Glue, por
exemplo), não existe um evento nativo de "a cópia entre buckets falhou"
pra uma regra do EventBridge escutar, então o alerta é publicado direto
daqui (mesmo padrão de lambda/quality_check.py, que também publica no
SNS a partir do próprio código).

Variáveis de ambiente esperadas: ORIGEM_BUCKET, ORIGEM_PADRAO,
ZIP_PREFIX, CLASSE_ATIVO_DEFAULT, FONTE_DEFAULT, BUCKET, SNS_TOPIC_ARN,
NOME_FONTE.
"""
import json
import os
import uuid
from datetime import datetime, timezone

import boto3

s3 = boto3.client("s3")
sns = boto3.client("sns")


def handler(event, context):
    event = event or {}
    data = event.get("data") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    classe_ativo = event.get("classe_ativo") or os.environ["CLASSE_ATIVO_DEFAULT"]
    fonte = event.get("fonte") or os.environ["FONTE_DEFAULT"]

    nome_arquivo = os.environ["ORIGEM_PADRAO"].format(
        fonte=fonte,
        fonte_upper=fonte.upper(),
        classe_ativo=classe_ativo,
        data_underscore=data.replace("-", "_"),
    )
    origem_bucket = os.environ["ORIGEM_BUCKET"]
    bucket = os.environ["BUCKET"]
    destino_key = f"{os.environ['ZIP_PREFIX']}{nome_arquivo.split('/')[-1]}"

    try:
        s3.copy_object(
            Bucket=bucket,
            Key=destino_key,
            CopySource={"Bucket": origem_bucket, "Key": nome_arquivo},
        )
    except Exception as erro:
        _alertar_falha(nome_arquivo, erro)
        raise

    _registrar_execucao(bucket, origem_bucket, nome_arquivo, destino_key)

    return {
        "origem": f"s3://{origem_bucket}/{nome_arquivo}",
        "destino": f"s3://{bucket}/{destino_key}",
    }


def _alertar_falha(nome_arquivo: str, erro: Exception) -> None:
    nome_fonte = os.environ.get("NOME_FONTE", "dtcc")
    sns.publish(
        TopicArn=os.environ["SNS_TOPIC_ARN"],
        Subject=f"[{nome_fonte}-pipeline] ingestão agendada falhou",
        Message=(
            f"Falha ao copiar {nome_arquivo} do bucket de origem -- "
            "possivelmente o arquivo de hoje ainda não foi publicado no "
            "horário agendado, ou o padrão de nome mudou.\n\n"
            f"Erro: {erro}"
        ),
    )


def _registrar_execucao(bucket: str, origem_bucket: str, nome_arquivo: str, destino_key: str) -> None:
    agora = datetime.now(timezone.utc)
    registro = {
        "timestamp": agora.isoformat(),
        "origem": "ingerir_cumulative",
        "zip": f"s3://{bucket}/{destino_key}",
        "origem_arquivo": f"s3://{origem_bucket}/{nome_arquivo}",
        "status": "copiado",
    }
    log_key = f"logs/execucoes/dt={agora.strftime('%Y-%m-%d')}/{uuid.uuid4()}.json"
    s3.put_object(Bucket=bucket, Key=log_key, Body=json.dumps(registro).encode("utf-8"))
