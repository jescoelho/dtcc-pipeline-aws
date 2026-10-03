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

Checagens, na ordem do que já vimos quebrar no dado real:
  1. Schema: as colunas-chave usadas pela Bronze/Silver estão presentes.
  2. Volume absoluto: o arquivo não está vazio.
  3. Domínio: todo valor de "Action type" está no conjunto conhecido
     (NEWT/MODI/CORR/TERM/EROR/REVI) -- um valor novo pode ser o DTCC
     mudando o layout, o que já aconteceu uma vez neste projeto.
  4. Volume relativo ao histórico: compara a contagem de linhas do
     arquivo de hoje com a média dos últimos DIAS_HISTORICO dias (lidos
     de logs/execucoes/, a própria tabela de controle que esta Lambda
     alimenta). Fecha a extensão futura que estava pendente desde a
     criação da tabela de controle -- antes, só dava pra comparar o
     arquivo isolado contra si mesmo (vazio ou não), não contra a
     tendência real.

Também grava um registro em logs/execucoes/ (tabela de controle
consultável no Athena, ver athena/queries.sql) com a contagem de linhas
e os problemas encontrados -- diferente do log do CloudWatch (texto
solto, só serve pra depurar um erro específico), isso é dado
estruturado: histórico de volume e qualidade por dia, consultável com
SQL.

Variável de ambiente esperada: SNS_TOPIC_ARN.
"""
import csv
import io
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

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

# Quantos dias de histórico olhar pra calcular a média de volume, e o
# quanto abaixo dela já conta como problema (0.5 = alerta se cair mais
# de 50% da média). Números redondos, de propósito -- calibrar com dado
# real é tarefa futura, não um valor definitivo.
DIAS_HISTORICO = 7
QUEDA_MAXIMA_TOLERADA = 0.5


def handler(event, context):
    bucket = event["detail"]["bucket"]["name"]
    key = event["detail"]["object"]["key"]
    execution_id = _buscar_execution_id(bucket, key)
    resultado = _checar(bucket, key)
    _registrar_execucao(bucket, resultado, execution_id)
    return {"checado": resultado}


def _buscar_execution_id(bucket: str, key: str) -> str:
    """Mesmo execution_id que o trigger_bronze.py lê -- ver o docstring
    lá para a explicação completa de como ele nasce e se propaga."""
    cabecalho = s3.head_object(Bucket=bucket, Key=key)
    return cabecalho.get("Metadata", {}).get("execution-id") or str(uuid.uuid4())


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

    baseline = _media_historica(bucket)
    if baseline is not None:
        limite = baseline * (1 - QUEDA_MAXIMA_TOLERADA)
        if linhas < limite:
            problemas.append(
                f"volume muito abaixo do histórico: {linhas} linhas "
                f"(média dos últimos {DIAS_HISTORICO} dias: {baseline:.0f})"
            )

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


def _media_historica(bucket: str, dias: int = DIAS_HISTORICO):
    """Lê os registros de quality_check dos últimos `dias` dias (de
    logs/execucoes/, a mesma tabela de controle que esta Lambda escreve)
    e devolve a média de linhas. None se não houver histórico ainda
    (primeiros dias do pipeline) -- nesse caso, a checagem é
    simplesmente pulada, não vira falso alerta.

    Limite assumido: inclui dias com problema no cálculo da média (não
    filtra por status == "ok") -- uma queda real e sustentada rebaixa a
    própria média que a detectaria, então uma degradação gradual (não
    um salto único) pode passar sem alertar. Comparar com uma baseline
    mais robusta (mediana, ou só dias "ok") é extensão futura.
    """
    hoje = datetime.now(timezone.utc).date()
    volumes = []
    paginador = s3.get_paginator("list_objects_v2")

    for i in range(1, dias + 1):
        dia = hoje - timedelta(days=i)
        prefixo = f"logs/execucoes/dt={dia.isoformat()}/"
        for pagina in paginador.paginate(Bucket=bucket, Prefix=prefixo):
            for item in pagina.get("Contents", []):
                corpo = s3.get_object(Bucket=bucket, Key=item["Key"])["Body"].read()
                registro = json.loads(corpo)
                if registro.get("origem") == "quality_check" and registro.get("linhas") is not None:
                    volumes.append(registro["linhas"])

    if not volumes:
        return None
    return sum(volumes) / len(volumes)


def _registrar_execucao(bucket: str, resultado: dict, execution_id: str) -> None:
    agora = datetime.now(timezone.utc)
    registro = {
        "timestamp": agora.isoformat(),
        "origem": "quality_check",
        "csv": resultado["csv"],
        "linhas": resultado["linhas"],
        "problemas": resultado["problemas"],
        "status": "problema" if resultado["problemas"] else "ok",
        "execution_id": execution_id,
    }
    log_key = f"logs/execucoes/dt={agora.strftime('%Y-%m-%d')}/{uuid.uuid4()}.json"
    s3.put_object(Bucket=bucket, Key=log_key, Body=json.dumps(registro).encode("utf-8"))
