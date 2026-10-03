"""Lambda disparada por evento do EventBridge: confere qualidade básica
do CSV que acabou de chegar em raw/dtcc/, ANTES (em paralelo, não
bloqueando) do Glue processar.

Alvo de aws_cloudwatch_event_rule.csv_arrived (terraform/lambda.tf),
MESMA regra que dispara lambda/trigger_bronze.py -- as duas são alvos
independentes de um único evento do EventBridge, nenhuma espera a outra.
Formato do evento é o "S3 Object Created" nativo do EventBridge (ver
trigger_bronze.py para a explicação completa de por que não é o formato
{"Records": [...]} do S3 direto):

  {
    "detail-type": "Object Created",
    "source": "aws.s3",
    "detail": {
      "bucket": {"name": "..."},
      "object": {"key": "..."}
    }
  }

Responsabilidade única -- só isso. Não dispara o Glue (isso é
lambda/trigger_bronze.py) e não descompacta (lambda/unzip_dtcc.py). Se o
arquivo tiver problema, publica no mesmo tópico SNS do alerta de falha do
Glue (terraform/observabilidade.tf); não bloqueia a ingestão, só avisa.

Checagens (as três mais simples que capturam o essencial, na ordem do
que já vimos quebrar no dado real):
  1. Schema: as colunas-chave usadas pela Bronze/Silver estão presentes.
  2. Volume: o arquivo não está vazio.
  3. Domínio: todo valor de "Action type" está no conjunto conhecido
     (NEWT/MODI/CORR/TERM/EROR/REVI) -- um valor novo pode ser o DTCC
     mudando o layout, o que já aconteceu uma vez neste projeto.

Também grava um registro em logs/execucoes/ (tabela de controle
consultável no Athena, ver athena/queries.sql) com a contagem de linhas
e os problemas encontrados -- diferente do log do CloudWatch (texto
solto, só serve pra depurar um erro específico), isso é dado
estruturado: histórico de volume e qualidade por dia, consultável com
SQL, base pra detectar queda gradual (tarefa futura já registrada no
README).

Variável de ambiente esperada: SNS_TOPIC_ARN.
"""
import csv
import io
import json
import os
import uuid
from datetime import datetime, timezone

import boto3

s3 = boto3.client("s3")
sns = boto3.client("sns")

COLUNAS_ESPERADAS = {
    "Dissemination Identifier",
    "Original Dissemination Identifier",
    "Action type",
    "Event timestamp",
}
ACTION_TYPES_ESPERADOS = {"NEWT", "MODI", "CORR", "TERM", "EROR", "REVI"}
LINHAS_MINIMAS = 1


def handler(event, context):
    bucket = event["detail"]["bucket"]["name"]
    key = event["detail"]["object"]["key"]
    resultado = _checar(bucket, key)
    _registrar_execucao(bucket, resultado)
    return {"checado": resultado}


def _checar(bucket: str, key: str) -> dict:
    obj = s3.get_object(Bucket=bucket, Key=key)
    texto = obj["Body"].read().decode("utf-8-sig")
    leitor = csv.DictReader(io.StringIO(texto))
    colunas = set(leitor.fieldnames or [])
    problemas = []

    faltando = COLUNAS_ESPERADAS - colunas
    if faltando:
        problemas.append(f"colunas ausentes: {sorted(faltando)}")

    linhas = 0
    action_types_vistos = set()
    for linha in leitor:
        linhas += 1
        action_types_vistos.add(linha.get("Action type", ""))

    if linhas < LINHAS_MINIMAS:
        problemas.append(f"arquivo com {linhas} linhas (esperado >= {LINHAS_MINIMAS})")

    inesperados = action_types_vistos - ACTION_TYPES_ESPERADOS - {""}
    if inesperados:
        problemas.append(f"Action type inesperado: {sorted(inesperados)}")

    resultado = {"csv": f"s3://{bucket}/{key}", "linhas": linhas, "problemas": problemas}

    if problemas:
        sns.publish(
            TopicArn=os.environ["SNS_TOPIC_ARN"],
            Subject=f"[dtcc-pipeline] qualidade de dados: {key}",
            Message="Problemas encontrados em s3://{}/{}:\n- {}".format(
                bucket, key, "\n- ".join(problemas)
            ),
        )

    return resultado


def _registrar_execucao(bucket: str, resultado: dict) -> None:
    agora = datetime.now(timezone.utc)
    registro = {
        "timestamp": agora.isoformat(),
        "origem": "quality_check",
        "csv": resultado["csv"],
        "linhas": resultado["linhas"],
        "problemas": resultado["problemas"],
        "status": "problema" if resultado["problemas"] else "ok",
    }
    log_key = f"logs/execucoes/dt={agora.strftime('%Y-%m-%d')}/{uuid.uuid4()}.json"
    s3.put_object(Bucket=bucket, Key=log_key, Body=json.dumps(registro).encode("utf-8"))
