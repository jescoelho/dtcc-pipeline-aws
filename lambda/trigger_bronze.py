"""Lambda disparada por evento do S3: inicia o job Glue Bronze quando um
.csv aparece em raw/dtcc/.

Responsabilidade única -- só isso. Não descompacta (isso é
lambda/unzip_dtcc.py) e não faz checagem de qualidade (tarefa futura, ver
README.md). Separada da Lambda de descompactar de propósito: se o
start_job_run falhar, isso não deve ser confundido com uma falha na
descompactação, que já tinha funcionado.

Variável de ambiente esperada: GLUE_JOB_NAME.
"""
import os

import boto3

glue = boto3.client("glue")


def handler(event, context):
    job_name = os.environ["GLUE_JOB_NAME"]
    resultados = []
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        response = glue.start_job_run(JobName=job_name)
        resultados.append({"csv": f"s3://{bucket}/{key}", "job_run_id": response["JobRunId"]})
    return {"disparados": resultados}
