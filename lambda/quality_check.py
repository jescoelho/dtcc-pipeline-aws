"""Lambda disparada por evento do S3: confere qualidade básica do CSV que
acabou de chegar em raw/dtcc/, ANTES (em paralelo, não bloqueando) do
Glue processar.

Responsabilidade única -- só isso. Não dispara o Glue (isso é
lambda/trigger_bronze.py) e não descompacta (lambda/unzip_dtcc.py).
Reage ao mesmo evento do S3 que a trigger_bronze, mas como alvo
independente dentro da mesma notificação -- as duas rodam em paralelo,
sem uma esperar a outra. Se o arquivo tiver problema, publica no mesmo
tópico SNS do alerta de falha do Glue (terraform/observabilidade.tf);
não bloqueia a ingestão, só avisa.

Checagens (as três mais simples que capturam o essencial, na ordem do
que já vimos quebrar no dado real):
  1. Schema: as colunas-chave usadas pela Bronze/Silver estão presentes.
  2. Volume: o arquivo não está vazio.
  3. Domínio: todo valor de "Action type" está no conjunto conhecido
     (NEWT/MODI/CORR/TERM/EROR/REVI) -- um valor novo pode ser o DTCC
     mudando o layout, o que já aconteceu uma vez neste projeto.

Variável de ambiente esperada: SNS_TOPIC_ARN.
"""
import csv
import io
import os

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
    resultados = []
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        resultados.append(_checar(bucket, key))
    return {"checados": resultados}


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
