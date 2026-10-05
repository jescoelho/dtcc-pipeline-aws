"""Lambda disparada por evento nativo do EventBridge (Glue Job State
Change) quando o job Glue Bronze termina, com qualquer desfecho
(sucesso, falha, timeout, parado).

Alvo de aws_cloudwatch_event_rule.glue_bronze_concluido
(terraform/modules/fonte/observabilidade.tf) -- regra independente da que
já avisa por e-mail em caso de falha
(aws_cloudwatch_event_rule.glue_bronze_failed): aquela só existe pra
FAILED/TIMEOUT/ERROR e manda e-mail; esta grava TODO término (inclusive
SUCCEEDED) na tabela de controle. Sem isso, a tabela só tinha as etapas
anteriores -- nunca o desfecho --, e não dava pra responder "esse CSV
processou com sucesso?" sem sair da tabela e consultar o Glue direto
(achado da avaliação end-to-end, ver README).

Formato do evento, nativo do Glue (diferente do "S3 Object Created" que
as outras Lambdas deste pipeline recebem):

  {
    "source": "aws.glue",
    "detail-type": "Glue Job State Change",
    "detail": {
      "jobName": "...",
      "jobRunId": "jr_...",
      "state": "SUCCEEDED" | "FAILED" | "TIMEOUT" | "STOPPED" | "ERROR"
    }
  }

Esse evento não carrega os argumentos com que o job foi iniciado -- por
isso busca o run completo via glue:GetJobRun, de onde vem:
  - o execution_id (propagado como argumento --execution_id pela
    state machine, vindo do unzip_dtcc.py), pra correlacionar com o
    registro de descompactação e com o de quality_check;
  - a duração real da execução (ExecutionTime, em segundos).

Variável de ambiente esperada: BUCKET (o evento do Glue não traz o nome
do bucket -- só as outras Lambdas, disparadas por evento do S3, têm isso
de graça).
"""
import json
import os
import uuid
from datetime import datetime, timezone

import boto3

glue = boto3.client("glue")
s3 = boto3.client("s3")


def handler(event, context):
    """Registra o desfecho de um run do job Glue na tabela de controle.

    O evento não traz os argumentos do run, então busca o run completo via
    glue:GetJobRun para recuperar o execution_id e a duração.

    Args:
        event: evento EventBridge "Glue Job State Change"
            (detail.jobName, detail.jobRunId, detail.state).
        context: contexto Lambda (não usado).

    Returns:
        {"concluido": {"job_run_id", "state", "execution_id",
        "duracao_segundos"}}; execution_id é None em runs manuais.
    """
    detail = event["detail"]
    job_name = detail["jobName"]
    job_run_id = detail["jobRunId"]
    state = detail["state"]

    run = glue.get_job_run(JobName=job_name, RunId=job_run_id)["JobRun"]
    execution_id = run.get("Arguments", {}).get("--execution_id")
    duracao_segundos = run.get("ExecutionTime")

    _registrar_execucao(job_run_id, state, execution_id, duracao_segundos)

    return {
        "concluido": {
            "job_run_id": job_run_id,
            "state": state,
            "execution_id": execution_id,
            "duracao_segundos": duracao_segundos,
        }
    }


def _registrar_execucao(job_run_id: str, state: str, execution_id, duracao_segundos) -> None:
    """Grava em logs/execucoes/ um registro JSON do tipo "glue_job" (um objeto
    por evento, em dt=AAAA-MM-DD/<uuid>.json), consultável no Athena via
    athena/queries.sql.

    Campos: timestamp (ISO 8601, UTC), origem ("glue_job"), status (estado
    do Glue em minúsculas, ex. "succeeded"), job_run_id, execution_id e
    duracao_segundos.

    O bucket vem da variável de ambiente BUCKET.

    Args:
        job_run_id: id do run do Glue (jr_...).
        state: estado final do job (SUCCEEDED, FAILED, TIMEOUT, STOPPED, ERROR).
        execution_id: uuid do fluxo, ou None se o run foi manual.
        duracao_segundos: ExecutionTime do run, ou None se não informado.
    """
    bucket = os.environ["BUCKET"]
    agora = datetime.now(timezone.utc)
    registro = {
        "timestamp": agora.isoformat(),
        "origem": "glue_job",
        "job_run_id": job_run_id,
        "execution_id": execution_id,
        "status": state.lower(),
        "duracao_segundos": duracao_segundos,
    }
    log_key = f"logs/execucoes/dt={agora.strftime('%Y-%m-%d')}/{uuid.uuid4()}.json"
    s3.put_object(Bucket=bucket, Key=log_key, Body=json.dumps(registro).encode("utf-8"))
