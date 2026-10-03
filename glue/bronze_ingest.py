"""Job Glue 1/2 — Bronze: ingestão crua do CSV Cumulative do DTCC PPD
(equivalente a src/dtcc/bronze.py).

Lê o CSV com header (Spark reconhece as 110 colunas pelo próprio
cabeçalho do arquivo — dividir em colunas não é interpretação, o arquivo
já declara a estrutura). Nenhum valor é convertido; tudo permanece string,
inclusive campos vazios. Adiciona proveniência (arquivo_origem,
line_number, ingerido_em) e particiona por arquivo_origem.

Argumentos:
  --raw_path     s3://bucket/raw/dtcc/CFTC_CUMULATIVE_RATES_AAAA_MM_DD.csv
  --bronze_path  s3://bucket/bronze/dtcc/
"""
import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import functions as F
from pyspark.sql.window import Window

args = getResolvedOptions(sys.argv, ["JOB_NAME", "raw_path", "bronze_path"])
sc = SparkContext()
glue = GlueContext(sc)
spark = glue.spark_session
job = Job(glue)
job.init(args["JOB_NAME"], args)

df = spark.read.option("header", "true").option("multiLine", "true").csv(args["raw_path"])

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
