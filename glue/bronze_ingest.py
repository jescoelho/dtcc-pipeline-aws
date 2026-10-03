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

Argumentos:
  --raw_path     s3://bucket/raw/dtcc/   (pasta, não um arquivo específico)
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

dyf = glue.create_dynamic_frame.from_options(
    connection_type="s3",
    connection_options={"paths": [args["raw_path"]], "recurse": True},
    format="csv",
    format_options={"withHeader": True, "multiline": True},
    transformation_ctx="raw_dtcc_source",
)
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
