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

**AWS Glue Data Quality (DQDL)**, validado com dado real em 03/10/2026
(RowCount=27133 bateu exatamente com a contagem em Python do
quality_check.py, mesmo veredicto "ok") e adotado: desde então é quem
cobre completude, validade de domínio (Action type) e unicidade --
dimensões que antes eram código Python escrito à mão no
quality_check.py, aposentado dessas checagens (ver docstring de
lambda/quality_check.py). Rodado aqui (dentro do próprio job Glue, via
o transform EvaluateDataQuality) em vez de numa Lambda nova, porque é
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

Cada regra do ruleset gera UM registro em logs/execucoes/ (formato
tidy: colunas regra/outcome/motivo_falha/metrica_nome/metrica_valor,
ver _registrar_data_quality) -- não um único registro com um blob json
de todas as regras, pra poder filtrar/agrupar por regra direto em SQL.
Diferente das Lambdas (testadas com pytest e mocks), não há como rodar
Spark/Glue Data Quality localmente -- a execução real na AWS é o teste.

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
from awsglue.transforms import SelectFromCollection
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

# ---------- Glue Data Quality ----------
# Cobre schema, domínio de Action type e unicidade -- as checagens que
# existiam em Python puro no quality_check.py antes da aposentadoria
# (ver docstring acima e docstring de lambda/quality_check.py).
# Limiares (0.99 de unicidade) são números redondos de partida, não
# calibrados.
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

dq_colecao = EvaluateDataQuality().process_rows(
    frame=dyf,
    ruleset=ruleset_dq,
    publishing_options={
        "dataQualityEvaluationContext": "dq_bronze_ingest",
        "enableDataQualityCloudWatchMetrics": True,
        "enableDataQualityResultsPublishing": False,  # ver docstring: decisão de escopo
    },
    additional_options={"performanceTuning.caching": "CACHE_NOTHING"},
)
# process_rows devolve uma DynamicFrameCollection, não um DynamicFrame
# direto -- erro real encontrado na primeira execução (AttributeError:
# 'DynamicFrameCollection' object has no attribute 'toDF'). A chave
# "ruleOutcomes" é o DynamicFrame com o veredicto de cada regra; é o que
# o Glue Studio gera quando você monta isso visualmente, e é o único
# jeito documentado de extrair de uma coleção.
dq_resultado = SelectFromCollection.apply(
    dfc=dq_colecao, key="ruleOutcomes", transformation_ctx="dq_rule_outcomes"
)


def _registrar_data_quality(dq_resultado, raw_path: str, execution_id) -> None:
    """Grava UM registro por regra avaliada -- não um blob único com as
    8 regras dentro de uma coluna "regras" (ver histórico: era assim
    antes, e exigia json_extract pra qualquer consulta). Tabela "tidy":
    uma informação por coluna, filtra e agrupa direto em SQL
    (`WHERE outcome = 'Failed'`, `GROUP BY regra`), sem parsear nada.

    Cada linha do resultado do Glue Data Quality tem no máximo UMA
    métrica em EvaluatedMetrics neste ruleset (confirmado com dado real
    -- ColumnExists não tem métrica nenhuma, as demais têm exatamente
    uma: Completeness, RowCount, ColumnValues.Compliance, Uniqueness).
    Por isso metrica_nome/metrica_valor cabem em colunas simples; se
    uma regra futura trouxer mais de uma métrica, só a primeira seria
    capturada aqui -- limite aceito por simplicidade, não validado
    contra esse caso."""
    linhas = [row.asDict() for row in dq_resultado.toDF().collect()]
    bucket = urlparse(raw_path).netloc
    agora = datetime.now(timezone.utc)
    s3_cliente = boto3.client("s3")

    for linha in linhas:
        metricas = linha.get("EvaluatedMetrics") or {}
        metrica_nome = next(iter(metricas), None)
        metrica_valor = metricas.get(metrica_nome) if metrica_nome is not None else None
        outcome = linha.get("Outcome")

        registro = {
            "timestamp": agora.isoformat(),
            "origem": "glue_data_quality",
            "execution_id": execution_id,
            "regra": linha.get("Rule"),
            "outcome": outcome,
            "motivo_falha": linha.get("FailureReason"),
            "metrica_nome": metrica_nome,
            "metrica_valor": metrica_valor,
            "status": "ok" if outcome == "Passed" else "problema",
        }
        log_key = f"logs/execucoes/dt={agora.strftime('%Y-%m-%d')}/{uuid.uuid4()}.json"
        s3_cliente.put_object(
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
