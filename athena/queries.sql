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


-- ============================================================
-- Tabela de controle: um registro por execução das Lambdas
-- trigger_bronze e quality_check (ver lambda/trigger_bronze.py e
-- lambda/quality_check.py), escrito em logs/execucoes/dt=AAAA-MM-DD/,
-- um JSON por evento. Diferente do log do CloudWatch (texto solto, só
-- serve pra depurar um erro específico), isso é dado estruturado:
-- consultável com SQL, barato de guardar por anos, base pra tendência
-- de volume e taxa de problemas ao longo do tempo.
-- ============================================================

CREATE EXTERNAL TABLE IF NOT EXISTS <DATABASE>.controle_execucoes (
  `timestamp` string,
  origem      string,
  csv         string,
  job_run_id  string,
  linhas      int,
  problemas   array<string>,
  status      string
)
PARTITIONED BY (dt string)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
LOCATION 's3://<BUCKET>/logs/execucoes/';

MSCK REPAIR TABLE <DATABASE>.controle_execucoes;

-- Volume processado por dia (só os registros da quality_check, que tem
-- a contagem de linhas -- a trigger_bronze só sabe o job_run_id).
SELECT dt, SUM(linhas) AS linhas_total, COUNT(*) AS arquivos
FROM <DATABASE>.controle_execucoes
WHERE origem = 'quality_check'
GROUP BY dt
ORDER BY dt;

-- Taxa de problemas de qualidade por dia -- é a extensão futura que
-- faltava na checagem de qualidade (comparar com o histórico), agora
-- possível porque o histórico existe e é consultável.
SELECT dt,
       COUNT(*) AS total_arquivos,
       SUM(CASE WHEN status = 'problema' THEN 1 ELSE 0 END) AS com_problema
FROM <DATABASE>.controle_execucoes
WHERE origem = 'quality_check'
GROUP BY dt
ORDER BY dt;

-- Todo disparo do Glue Bronze, com o job_run_id pra cruzar com
-- `aws glue get-job-run --run-id ...` se precisar investigar um run
-- específico.
SELECT dt, csv, job_run_id, "timestamp"
FROM <DATABASE>.controle_execucoes
WHERE origem = 'trigger_bronze'
ORDER BY "timestamp" DESC;

-- Nota: MSCK REPAIR precisa rodar de novo pra enxergar partições
-- (dt=...) novas -- o mesmo limite que já existia na tabela dtcc_bronze.
-- Rodar isso diariamente (ou antes de consultar) é manual por enquanto.
