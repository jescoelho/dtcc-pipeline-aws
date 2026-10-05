"""Lambda invocada pela state machine (terraform/modules/fonte/step_functions.tf):
confere o volume do CSV que acabou de chegar em raw/dtcc/ contra o
histórico recente, em paralelo (não bloqueando) com o job Glue.

É o Task ChecarQualidade, num ramo do Parallel ao lado do Glue -- os
dois ramos são independentes, nenhum espera o outro. A state machine
monta o evento no mesmo formato do "S3 Object Created" do EventBridge
(bucket do evento original, key do CSV devolvido pelo unzip_dtcc):

  {
    "detail-type": "Object Created",
    "source": "aws.s3",
    "detail": {
      "bucket": {"name": "..."},
      "object": {"key": "..."}
    }
  }

Responsabilidade única -- só isso. Não dispara o Glue (isso é a
state machine) e não descompacta (lambda/unzip_dtcc.py). Se o
volume cair abaixo do histórico, publica no mesmo tópico SNS do alerta
de falha do Glue (terraform/observabilidade.tf); não bloqueia a
ingestão, só avisa.

APOSENTADO desta Lambda (03/10/2026): as checagens de schema
(colunas presentes) e domínio (Action type no conjunto conhecido) que
existiam aqui em Python puro foram desligadas depois de validar, com
dado real, que o **AWS Glue Data Quality** (DQDL, ver
glue/bronze_ingest.py) cobre essas duas dimensões de forma superior --
métricas quantificadas (`Completeness`, `ColumnValues.Compliance`),
não só um booleano, e sem manter regra de negócio duplicada em dois
lugares (Python aqui, DQDL lá). Esta Lambda ficou só com o que o Glue
Data Quality **não faz**:
  1. Comparação de volume contra o histórico (baseline de N dias) --
     o Glue DQ só vê o arquivo de hoje, isolado; não tem memória do
     que já passou.
  2. O alerta em si (publish no SNS) -- o Glue DQ só grava métrica,
     não dispara notificação.
Nada de schema/domínio é checado aqui -- isso seria redundante com as
regras `ColumnExists`/`ColumnValues` do ruleset DQDL.

Também grava um registro em logs/execucoes/ (tabela de controle
consultável no Athena, ver athena/queries.sql) com a contagem de linhas
-- diferente do log do CloudWatch (texto solto, só serve pra depurar um
erro específico), isso é dado estruturado: histórico de volume por dia,
consultável com SQL, e é a própria fonte que `_media_historica` lê.

Consistência sazonal (04/10/2026): além da média simples dos últimos
DIAS_HISTORICO dias corridos, compara o volume de hoje contra a média do
MESMO dia da semana (ex.: segunda contra as últimas SEMANAS_HISTORICO_SAZONAL
segundas) quando CONSISTENCIA_SAZONAL estiver ligado -- lacuna registrada
desde a criação desta checagem: uma fonte cujo volume varia
previsivelmente por dia da semana não deveria ser comparada contra uma
média que mistura todos os dias. Cai pra média simples automaticamente
quando ainda não há ocorrências suficientes do mesmo dia da semana
(pipeline recente) -- ver _escolher_baseline.

Variáveis de ambiente esperadas: SNS_TOPIC_ARN, DIAS_HISTORICO,
QUEDA_MAXIMA_TOLERADA, CONSISTENCIA_SAZONAL e SEMANAS_HISTORICO_SAZONAL --
as quatro últimas vêm do contrato de fonte (config/fontes/dtcc.yaml, ver
terraform/quality_check.tf), não são constantes cravadas aqui. Defaults
locais só pra não quebrar se a env var faltar (ex.: rodando fora do
Lambda) -- em produção sempre vêm do Terraform.
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

# Quantos dias de histórico olhar pra calcular a média de volume, e o
# quanto abaixo dela já conta como problema (0.5 = alerta se cair mais
# de 50% da média). Vêm do contrato de fonte; os defaults abaixo só
# cobrem execução fora do Lambda (ex.: teste local sem monkeypatch).
DIAS_HISTORICO = int(os.environ.get("DIAS_HISTORICO", "7"))
QUEDA_MAXIMA_TOLERADA = float(os.environ.get("QUEDA_MAXIMA_TOLERADA", "0.5"))
# Consistência sazonal (comparar contra o mesmo dia da semana, não uma
# média que mistura todos os dias) -- parametrizável por fonte; default
# local liga por padrão, mas em produção sempre vem do contrato.
CONSISTENCIA_SAZONAL = os.environ.get("CONSISTENCIA_SAZONAL", "true").lower() == "true"
SEMANAS_HISTORICO_SAZONAL = int(os.environ.get("SEMANAS_HISTORICO_SAZONAL", "4"))
# Nome da fonte (contrato de fonte, local.fonte.nome) -- usado só pra
# identificar o remetente no assunto do e-mail de alerta. Antes era
# "[dtcc-pipeline]" cravado; default "dtcc" preserva o texto de hoje.
NOME_FONTE = os.environ.get("NOME_FONTE", "dtcc")


def handler(event, context):
    """Confere o volume do CSV contra o histórico, sem bloquear o Glue.

    Args:
        event: evento no formato "S3 Object Created" do EventBridge
            (detail.bucket.name e detail.object.key do CSV).
        context: contexto Lambda (não usado).

    Returns:
        {"checado": resultado}, onde resultado é o dict de `_checar`
        (csv, linhas, problemas).
    """
    bucket = event["detail"]["bucket"]["name"]
    key = event["detail"]["object"]["key"]
    execution_id = _buscar_execution_id(bucket, key)
    resultado = _checar(bucket, key)
    _registrar_execucao(bucket, resultado, execution_id)
    return {"checado": resultado}


def _buscar_execution_id(bucket: str, key: str) -> str:
    """Lê o execution_id do metadado do CSV, gravado pelo unzip_dtcc.py
    (ver o docstring lá para como ele nasce e se propaga); gera um uuid
    novo se o metadado não existir."""
    cabecalho = s3.head_object(Bucket=bucket, Key=key)
    return cabecalho.get("Metadata", {}).get("execution-id") or str(uuid.uuid4())


def _checar(bucket: str, key: str) -> dict:
    """Conta as linhas do CSV, compara com a baseline histórica e publica no SNS se
    houver problema; devolve {'csv', 'linhas', 'problemas'}.
    """
    obj = s3.get_object(Bucket=bucket, Key=key)
    texto = obj["Body"].read().decode("utf-8-sig")
    # csv.DictReader (não um split/count de linha) porque o CSV pode ter
    # campos com quebra de linha dentro de aspas -- contar "\n" seria
    # errado. Não usamos os valores de cada linha pra nada além de
    # contar -- schema e domínio são responsabilidade do Glue Data
    # Quality agora (ver docstring do módulo).
    leitor = csv.DictReader(io.StringIO(texto))
    linhas = sum(1 for _ in leitor)

    problemas = []
    baseline, rotulo = _escolher_baseline(bucket)
    if baseline is not None:
        limite = baseline * (1 - QUEDA_MAXIMA_TOLERADA)
        if linhas < limite:
            problemas.append(
                f"volume muito abaixo do histórico ({rotulo}): {linhas} linhas "
                f"(média: {baseline:.0f})"
            )

    resultado = {"csv": f"s3://{bucket}/{key}", "linhas": linhas, "problemas": problemas}

    if problemas:
        sns.publish(
            TopicArn=os.environ["SNS_TOPIC_ARN"],
            Subject=f"[{NOME_FONTE}-pipeline] qualidade de dados: {key}",
            Message="Problemas encontrados em s3://{}/{}:\n- {}".format(
                bucket, key, "\n- ".join(problemas)
            ),
        )

    return resultado


def _escolher_baseline(bucket: str):
    """Decide qual baseline usar pra comparar o volume de hoje, e devolve
    (media, rotulo) -- ou (None, None) se não houver histórico nenhum
    ainda (nesse caso a checagem de volume relativo é simplesmente
    pulada, não vira falso alerta).

    Tenta primeiro a consistência sazonal (mesmo dia da semana), se
    ligada pelo contrato de fonte (CONSISTENCIA_SAZONAL); cai pra média
    simples dos últimos DIAS_HISTORICO dias corridos se a sazonal ainda
    não tiver ocorrências suficientes (pipeline recente) ou estiver
    desligada -- nunca fica sem nenhuma checagem por falta de dado
    sazonal.
    """
    if CONSISTENCIA_SAZONAL:
        # Olhar pra trás `semanas * 7` dias corridos, filtrando só os que
        # cairam no mesmo dia da semana de hoje, cobre exatamente as
        # últimas `semanas` ocorrências desse dia da semana.
        sazonal = _media_historica(
            bucket, dias=SEMANAS_HISTORICO_SAZONAL * 7, mesmo_dia_semana=True
        )
        if sazonal is not None:
            return sazonal, f"últimas {SEMANAS_HISTORICO_SAZONAL} ocorrências do mesmo dia da semana"

    simples = _media_historica(bucket, dias=DIAS_HISTORICO)
    if simples is not None:
        return simples, f"média dos últimos {DIAS_HISTORICO} dias corridos"
    return None, None


def _media_historica(bucket: str, dias: int = DIAS_HISTORICO, mesmo_dia_semana: bool = False):
    """Lê os registros de quality_check dos últimos `dias` dias corridos
    (de logs/execucoes/, a mesma tabela de controle que esta Lambda
    escreve) e devolve a média de linhas, ou None se não encontrar
    nenhum registro na janela.

    mesmo_dia_semana: se True, só considera dias cujo dia da semana
    (segunda, terça, ...) seja igual ao de hoje -- é o que torna `dias`
    uma janela em dias CORRIDOS equivalente a `dias // 7` ocorrências
    desse dia da semana (consistência sazonal, ver _escolher_baseline).
    Dias fora do dia da semana nem chegam a consultar o S3.

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
        if mesmo_dia_semana and dia.weekday() != hoje.weekday():
            continue
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
    """Grava em logs/execucoes/ um registro JSON do tipo "quality_check" (um objeto
    por evento, em dt=AAAA-MM-DD/<uuid>.json), consultável no Athena via
    athena/queries.sql.

    Campos: timestamp (ISO 8601, UTC), origem ("quality_check"), status
    ("ok" ou "problema"), csv, linhas, problemas e execution_id.

    Args:
        bucket: bucket do pipeline, onde o registro é gravado.
        resultado: dict devolvido por `_checar` (csv, linhas, problemas).
        execution_id: uuid que liga as etapas desta execução.
    """
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
