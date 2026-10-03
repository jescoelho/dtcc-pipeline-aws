"""Lambda disparada por evento do S3: descompacta o Cumulative do DTCC PPD.

Gatilho: objeto criado em raw/dtcc_zip/*.zip (ver terraform/lambda.tf).
Ação: lê o .zip inteiro pra memória (cabe bem dentro do limite da Lambda --
o arquivo real descompactado tem ~16 MB), acha o único .csv dentro dele, e
escreve esse .csv em raw/dtcc/ no mesmo bucket -- sem tocar em disco local
nenhum (nem o seu computador, nem a própria Lambda usa /tmp aqui).

Escopo desta etapa: só descompactar. Disparar o job Glue Bronze continua
sendo manual (ou via o script scripts/ingerir_cumulative.sh) por enquanto
-- próximo degrau de automação, não construído ainda.
"""
import io
import zipfile

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

    destino_key = f"raw/dtcc/{csv_name.split('/')[-1]}"
    s3.put_object(Bucket=bucket, Key=destino_key, Body=csv_bytes)

    return {"origem": key, "destino": destino_key, "bytes": len(csv_bytes)}
