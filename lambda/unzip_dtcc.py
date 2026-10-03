"""Lambda disparada por evento do S3: descompacta o Cumulative do DTCC PPD.

Gatilho: objeto criado em raw/dtcc_zip/*.zip (ver terraform/lambda.tf).
Ação: lê o .zip inteiro pra memória (cabe bem dentro do limite da Lambda --
o arquivo real descompactado tem ~16 MB), acha o único .csv dentro dele, e
escreve esse .csv em raw/dtcc/ no mesmo bucket -- sem tocar em disco local
nenhum (nem o seu computador, nem a própria Lambda usa /tmp aqui).

Primeira etapa do fluxo end-to-end (ver README, achado da avaliação de
observabilidade): gera um execution_id (uuid) aqui, que nasce com a
execução, e o anexa como metadado do objeto CSV que escreve
(`Metadata={"execution-id": ...}`). As Lambdas seguintes (trigger_bronze,
quality_check) leem esse metadado de volta via head_object, e o
trigger_bronze o propaga pro Glue como argumento de job
(`--execution_id`), de onde o job_concluido o recupera no fim. Resultado:
as quatro etapas do pipeline (descompactar -> disparar -> checar
qualidade -> Glue terminar) ficam ligadas pelo mesmo execution_id na
tabela de controle, sem depender de casar por nome de arquivo + horário.

Também grava um registro em logs/execucoes/ (tabela de controle
consultável no Athena, ver athena/queries.sql) -- sem isso, a chegada e
descompactação do .zip não deixava rastro nenhum fora do CloudWatch
(retenção de 14 dias, não consultável por SQL).

Escopo desta etapa: só descompactar. Disparar o job Glue Bronze é
responsabilidade de lambda/trigger_bronze.py.

O prefixo de destino (`raw/dtcc/`) vem da variável de ambiente
RAW_PREFIX (ver terraform/lambda.tf), que por sua vez vem do contrato de
fonte (config/fontes/dtcc.yaml) -- não é mais uma string cravada aqui,
pra esta Lambda poder servir qualquer fonte que descompacte um .zip com
um .csv dentro, não só o DTCC especificamente.

Variável de ambiente esperada: RAW_PREFIX.
"""
import io
import json
import os
import uuid
import zipfile
from datetime import datetime, timezone

import boto3

s3 = boto3.client("s3")


def handler(event, context):
    resultados = []
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        resultados.append(_processar(bucket, key))
    return {"processados": resultados}


def _processar(bucket: str, key: str) -> dict:
    obj = s3.get_object(Bucket=bucket, Key=key)
    zip_bytes = io.BytesIO(obj["Body"].read())

    with zipfile.ZipFile(zip_bytes) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if len(csv_names) != 1:
            raise ValueError(
                f"esperava exatamente 1 .csv dentro de {key}, achei {len(csv_names)}: {csv_names}"
            )
        csv_name = csv_names[0]
        with zf.open(csv_name) as csv_file:
            csv_bytes = csv_file.read()

    execution_id = str(uuid.uuid4())
    destino_key = f"{os.environ['RAW_PREFIX']}{csv_name.split('/')[-1]}"
    s3.put_object(
        Bucket=bucket,
        Key=destino_key,
        Body=csv_bytes,
        Metadata={"execution-id": execution_id},
    )

    _registrar_execucao(bucket, key, destino_key, execution_id)

    return {
        "origem": key,
        "destino": destino_key,
        "bytes": len(csv_bytes),
        "execution_id": execution_id,
    }


def _registrar_execucao(bucket: str, zip_key: str, csv_key: str, execution_id: str) -> None:
    agora = datetime.now(timezone.utc)
    registro = {
        "timestamp": agora.isoformat(),
        "origem": "unzip_dtcc",
        "zip": f"s3://{bucket}/{zip_key}",
        "csv": f"s3://{bucket}/{csv_key}",
        "execution_id": execution_id,
        "status": "descompactado",
    }
    log_key = f"logs/execucoes/dt={agora.strftime('%Y-%m-%d')}/{uuid.uuid4()}.json"
    s3.put_object(Bucket=bucket, Key=log_key, Body=json.dumps(registro).encode("utf-8"))
