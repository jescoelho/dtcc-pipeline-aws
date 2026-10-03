"""Job Glue 1/2 — Bronze: ingestão crua do CSV Cumulative do DTCC PPD
(equivalente a src/dtcc/bronze.py).

Lê o CSV com header (Spark reconhece as 110 colunas pelo próprio
cabeçalho do arquivo — dividir em colunas não é interpretação, o arquivo
já declara a estrutura). Nenhum valor é convertido; tudo permanece string,
inclusive campos vazios. Adiciona proveniência (arquivo_origem,
line_number, ingerido_em) e particiona por arquivo_origem.

**Job Bookmarks habilitado** (`--job-bookmark-option job-bookmark-enable`,
ver terraform/main.tf): sem isso, cada execução relia a pasta raw/dtcc/
inteira, mesmo os arquivos já processados em runs anteriores -- custo e
tempo crescendo sem necessidade a cada dia novo, e dois disparos próximos
reprocessando os mesmos dados concorrentemente. Com bookmarks, o Glue
rastreia quais arquivos já leu com sucesso e só processa os novos.

Isso exige ler via `glueContext.create_dynamic_frame.from_options` (com
`transformation_ctx` -- é essa chave que o bookmark usa para saber o que
já foi lido), não `spark.read.csv` direto: o bookmark é um recurso do
Glue, não do Spark puro, e só funciona através da API de DynamicFrame.

A escrita continua em `overwrite` com `partitionOverwriteMode=dynamic`:
isso já era idempotente antes dos bookmarks (reprocessar o mesmo arquivo
só sobrescreve a partição dele) e continua sendo -- os bookmarks reduzem
o que entra no DataFrame a cada run, não mudam a lógica de escrita.

**Protótipo: AWS Glue Data Quality (DQDL), rodando ao lado do
lambda/quality_check.py, não substituindo.** Ideia: comparar as duas
abordagens com dado real antes de decidir qual generalizar pra outras
fontes (ver README, seção de observabilidade genérica). O Glue Data
Quality cobre nativamente completude, validade de domínio e unicidade
-- dimensões que hoje são código Python escrito à mão no
quality_check.py. Rodado aqui (dentro do próprio job Glue, via o
transform EvaluateDataQuality) em vez de numa Lambda nova, porque é
assim que o serviço se integra a um job ETL -- não existe variante
"Lambda chama Glue Data Quality" sem reimplementar a avaliação.

Decisão de escopo: NÃO habilitamos
`enableDataQualityResultsPublishing` (o repositório nativo de
resultados do Glue, atrelado ao Catalog) para não precisar descobrir e
validar as permissões IAM adicionais que isso exige -- em vez disso,
publicamos nosso próprio resumo em logs/execucoes/ (a mesma tabela de
controle que trigger_bronze/quality_check/job_concluido já escrevem),
que já é consultável no Athena e cuja permissão de escrita o role do
Glue já tem (s3:PutObject no bucket inteiro, ver terraform/main.tf).
`enableDataQualityCloudWatchMetrics` fica habilitado -- publica
métricas de pass/fail por regra no CloudWatch, sem exigir permissão
nova além de cloudwatch:PutMetricData (adicionada ao role do Glue).

NÃO VALIDADO EM EXECUÇÃO REAL ainda -- diferente das Lambdas (testadas
com pytest e mocks), não há como rodar Spark/Glue Data Quality
localmente. A primeira execução real na AWS é o teste.

Argumentos:
  --raw_path       s3://bucket/raw/dtcc/   (pasta, não um arquivo específico)
  --bronze_path    s3://bucket/bronze/dtcc/
  --execution_id   opcional -- propagado pelo trigger_bronze.py (ver
                    lambda/trigger_bronze.py) pra correlacionar este
                    registro com o resto do fluxo end-to-end. Ausente em
                    execuções manuais (ex.: reprocessamento direto no
                    console), e tá tudo bem nesse caso.
"""
import json
import sys
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import boto3
from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from awsgluedq.transforms import EvaluateDataQuality
from pyspark.context import SparkContext
from pyspark.sql import functions as F
from pyspark.sql.window import Window

args = getResolvedOptions(sys.argv, ["JOB_NAME", "raw_path", "bronze_path"])
sc = SparkContext()
glue = GlueContext(sc)
spark = glue.spark_session
job = Job(glue)
job.init(args["JOB_NAME"], args)


def _argumento_opcional(nome: str):
    """getResolvedOptions quebra se pedir um argumento que não foi
    passado -- execution_id só existe quando o trigger_bronze.py chamou
    start_job_run com ele (ver lambda/trigger_bronze.py); uma execução
    manual não tem. Leitura direta do sys.argv em vez disso."""
    chave = f"--{nome}"
    if chave in sys.argv:
        return sys.argv[sys.argv.index(chave) + 1]
    return None


execution_id = _argumento_opcional("execution_id")

dyf = glue.create_dynamic_frame.from_options(
    connection_type="s3",
    connection_options={"paths": [args["raw_path"]], "recurse": True},
    format="csv",
    format_options={"withHeader": True, "multiline": True},
    transformation_ctx="raw_dtcc_source",
)

# ---------- Glue Data Quality (protótipo) ----------
# Mesmas regras que o quality_check.py já checa em Python puro (schema,
# domínio de Action type), mais Uniqueness (que o quality_check.py NÃO
# tem hoje) -- ver docstring acima pro raciocínio completo. Limiares
# (0.99 de unicidade) são números redondos de partida, não calibrados.
ruleset_dq = """
Rules = [
    ColumnExists "Dissemination Identifier",
    ColumnExists "Original Dissemination Identifier",
    ColumnExists "Action type",
    ColumnExists "Event timestamp",
    IsComplete "Dissemination Identifier",
    RowCount > 0,
    ColumnValues "Action type" in ["NEWT", "MODI", "CORR", "TERM", "EROR", "REVI"],
    Uniqueness "Dissemination Identifier" > 0.99
]
"""

dq_resultado = EvaluateDataQuality().process_rows(
    frame=dyf,
    ruleset=ruleset_dq,
    publishing_options={
        "dataQualityEvaluationContext": "dq_bronze_ingest",
        "enableDataQualityCloudWatchMetrics": True,
        "enableDataQualityResultsPublishing": False,  # ver docstring: decisão de escopo
    },
    additional_options={"performanceTuning.caching": "CACHE_NOTHING"},
)


def _registrar_data_quality(dq_resultado, raw_path: str, execution_id) -> None:
    """Grava o resumo do Glue Data Quality em logs/execucoes/, no mesmo
    formato que as outras etapas do pipeline usam -- pra comparar lado a
    lado com os registros de quality_check.py no Athena (mesma tabela,
    execution_id em comum quando disponível)."""
    linhas = [row.asDict() for row in dq_resultado.toDF().collect()]
    status = "ok" if all(r.get("Outcome") == "Passed" for r in linhas) else "problema"

    bucket = urlparse(raw_path).netloc
    agora = datetime.now(timezone.utc)
    registro = {
        "timestamp": agora.isoformat(),
        "origem": "glue_data_quality",
        "execution_id": execution_id,
        "status": status,
        # String JSON, não array<struct> nativo: as colunas que vêm em
        # cada linha de resultado variam por tipo de regra (Outcome,
        # FailureReason nem sempre presentes) -- serializar como texto
        # evita schema do Athena quebrar a cada regra nova. Quem
        # consultar usa json_extract.
        "regras": json.dumps(linhas, default=str),
    }
    log_key = f"logs/execucoes/dt={agora.strftime('%Y-%m-%d')}/{uuid.uuid4()}.json"
    boto3.client("s3").put_object(
        Bucket=bucket, Key=log_key, Body=json.dumps(registro, default=str).encode("utf-8")
    )


_registrar_data_quality(dq_resultado, args["raw_path"], execution_id)

# ---------- Ingestão em si (inalterada) ----------
df = dyf.toDF()

df = df.withColumn("arquivo_origem", F.element_at(F.split(F.input_file_name(), "/"), -1))

w = Window.partitionBy("arquivo_origem").orderBy(F.monotonically_increasing_id())
df = df.withColumn("line_number", F.row_number().over(w) - 1)
df = df.withColumn("ingerido_em", F.current_timestamp())

(
    df.write.mode("overwrite")
    .option("partitionOverwriteMode", "dynamic")
    .partitionBy("arquivo_origem")
    .parquet(args["bronze_path"])
)

job.commit()
