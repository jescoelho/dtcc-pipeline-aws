"""Lambda disparada por evento do EventBridge: inicia o job Glue Bronze
quando um .csv aparece em raw/dtcc/.

Alvo de aws_cloudwatch_event_rule.csv_arrived (terraform/lambda.tf), não
mais de uma notificação direta do S3 -- o CSV tem dois consumidores
independentes (esta e lambda/quality_check.py), e o S3 não aceita duas
Lambdas registradas no mesmo prefixo+sufixo. Por isso o formato do evento
é o "S3 Object Created" nativo do EventBridge, diferente do formato
{"Records": [...]} que o S3 manda quando invoca uma Lambda direto (ver
lambda/unzip_dtcc.py, que continua no formato antigo):

  {
    "detail-type": "Object Created",
    "source": "aws.s3",
    "detail": {
      "bucket": {"name": "..."},
      "object": {"key": "..."}
    }
  }

Responsabilidade única -- só isso. Não descompacta (isso é
lambda/unzip_dtcc.py) e não faz checagem de qualidade (isso é
lambda/quality_check.py, que roda em paralelo a partir da mesma regra).

Lê o execution_id de volta do metadado do objeto S3 (gravado pelo
unzip_dtcc.py) e o propaga pro Glue como argumento de job
(--execution_id), de onde o job_concluido.py o recupera quando o job
termina -- é o que liga "disparado" e "terminou com sucesso/falhou" na
tabela de controle sem depender de casar por nome de arquivo + horário.

Além de disparar o Glue, grava um registro em logs/execucoes/ (tabela de
controle consultável no Athena, ver athena/queries.sql) -- diferente do
log do CloudWatch (texto solto, só serve pra depurar um erro específico),
isso é dado estruturado: histórico de todo disparo, com job_run_id,
consultável com SQL, barato de guardar por anos.

Variável de ambiente esperada: GLUE_JOB_NAME.
"""
import json
import os
import uuid
from datetime import datetime, timezone

import boto3

glue = boto3.client("glue")
s3 = boto3.client("s3")


def handler(event, context):
    job_name = os.environ["GLUE_JOB_NAME"]
    bucket = event["detail"]["bucket"]["name"]
    key = event["detail"]["object"]["key"]

    execution_id = _buscar_execution_id(bucket, key)

    response = glue.start_job_run(
        JobName=job_name,
        Arguments={"--execution_id": execution_id},
    )
    job_run_id = response["JobRunId"]

    _registrar_execucao(bucket, key, job_run_id, execution_id)

    return {
        "disparado": {
            "csv": f"s3://{bucket}/{key}",
            "job_run_id": job_run_id,
            "execution_id": execution_id,
        }
    }


def _buscar_execution_id(bucket: str, key: str) -> str:
    """O execution_id nasce no unzip_dtcc.py e viaja como metadado do
    objeto S3 -- aqui só lê de volta. Se não existir (CSV colocado
    manualmente, sem passar pelo unzip -- ex.: durante um teste manual),
    gera um novo, pra nunca ficar sem correlação nenhuma; só não vai bater
    com nenhum registro de unzip_dtcc nesse caso, e tá tudo bem.
    """
    cabecalho = s3.head_object(Bucket=bucket, Key=key)
    return cabecalho.get("Metadata", {}).get("execution-id") or str(uuid.uuid4())


def _registrar_execucao(bucket: str, key: str, job_run_id: str, execution_id: str) -> None:
    agora = datetime.now(timezone.utc)
    registro = {
        "timestamp": agora.isoformat(),
        "origem": "trigger_bronze",
        "csv": f"s3://{bucket}/{key}",
        "job_run_id": job_run_id,
        "execution_id": execution_id,
        "status": "disparado",
    }
    log_key = f"logs/execucoes/dt={agora.strftime('%Y-%m-%d')}/{uuid.uuid4()}.json"
    s3.put_object(Bucket=bucket, Key=log_key, Body=json.dumps(registro).encode("utf-8"))
