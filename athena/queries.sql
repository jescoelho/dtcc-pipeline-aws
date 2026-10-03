-- Tabela sobre a Bronze do DTCC PPD -- útil para validar que a ingestão
-- rodou certo (contagem de linhas, Action type, proveniência), antes de
-- existir uma Silver. Troque <BUCKET> e <DATABASE> pelos outputs do
-- terraform.

CREATE EXTERNAL TABLE IF NOT EXISTS <DATABASE>.dtcc_bronze (
  `Dissemination Identifier` string,
  `Original Dissemination Identifier` string,
  `Action type` string,
  `Event type` string,
  `Event timestamp` string,
  `Asset Class` string,
  `Product name` string,
  `Notional amount-Leg 1` string,
  `Notional currency-Leg 1` string,
  line_number bigint,
  ingerido_em timestamp
)
PARTITIONED BY (arquivo_origem string)
STORED AS PARQUET
LOCATION 's3://<BUCKET>/bronze/dtcc/';

MSCK REPAIR TABLE <DATABASE>.dtcc_bronze;

-- Confere se o número de linhas bate com o CSV original.
SELECT arquivo_origem, count(*) AS linhas
FROM <DATABASE>.dtcc_bronze
GROUP BY arquivo_origem;

-- Distribuição de Action type -- confirma que a mistura NEWT/MODI/CORR/
-- TERM chegou intacta na Bronze.
SELECT `Action type`, count(*) AS total
FROM <DATABASE>.dtcc_bronze
GROUP BY `Action type`
ORDER BY total DESC;

-- Nota: o schema acima lista só um subconjunto das ~110 colunas reais
-- (só os 10 campos mais usados para validar a ingestão). As demais
-- colunas (pernas, opções, pacotes etc.) existem no Parquet, só não
-- foram declaradas aqui -- adicione conforme precisar, ou use um Glue
-- Crawler para descobrir o schema completo automaticamente.
