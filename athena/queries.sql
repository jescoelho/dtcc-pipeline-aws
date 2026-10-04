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

-- Schema cobre as 4 etapas do fluxo end-to-end (ver README, achado da
-- avaliação de observabilidade): unzip_dtcc (descompactou),
-- trigger_bronze (disparou o Glue), quality_check (checou qualidade) e
-- glue_job (o Glue terminou -- sucesso ou falha). Cada etapa grava um
-- subconjunto diferente das colunas; o execution_id é o que liga todas
-- as quatro de um mesmo arquivo. Se o schema mudar, rode
-- scripts/configurar_athena.sh de novo -- ele faz DROP+CREATE (só
-- metadado) nesta tabela, porque CREATE EXTERNAL TABLE IF NOT EXISTS não
-- atualiza o schema de uma tabela que já existe.
CREATE EXTERNAL TABLE IF NOT EXISTS <DATABASE>.controle_execucoes (
  `timestamp`      string,
  origem           string,
  csv              string,
  zip              string,
  job_run_id       string,
  execution_id     string,
  linhas           int,
  problemas        array<string>,
  status           string,
  duracao_segundos int,
  regra            string,
  outcome          string,
  motivo_falha     string,
  metrica_nome     string,
  metrica_valor    double
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
--
-- origem = 'trigger_bronze' só existe em registros ANTERIORES a
-- 03/10/2026 -- essa etapa foi aposentada quando o pipeline passou a
-- ser orquestrado por Step Functions (ver README, seção Step
-- Functions): quem dispara o Glue agora é a própria state machine
-- (integration glue:startJobRun.sync), sem Lambda nem registro
-- "disparado" separado. O desfecho continua vindo de origem='glue_job'
-- (job_concluido.py, que escuta o evento nativo do Glue e não mudou).
SELECT dt, csv, job_run_id, "timestamp"
FROM <DATABASE>.controle_execucoes
WHERE origem = 'trigger_bronze'
ORDER BY "timestamp" DESC;

-- Fluxo completo de uma execução, as etapas lado a lado -- a pergunta
-- que a tabela não respondia antes desta extensão ("esse CSV terminou
-- de processar com sucesso, e quanto tempo levou desde que chegou?").
-- disparado_em fica NULL em execuções a partir de 03/10/2026 (ver nota
-- da query acima) -- descompactado_em já marca o início do fluxo.
SELECT
  execution_id,
  MIN(CASE WHEN origem = 'unzip_dtcc'    THEN "timestamp" END) AS descompactado_em,
  MIN(CASE WHEN origem = 'trigger_bronze' THEN "timestamp" END) AS disparado_em,
  MIN(CASE WHEN origem = 'quality_check'  THEN status END)      AS qualidade,
  MIN(CASE WHEN origem = 'glue_job'       THEN status END)      AS desfecho_glue,
  MIN(CASE WHEN origem = 'glue_job'       THEN duracao_segundos END) AS duracao_glue_segundos
FROM <DATABASE>.controle_execucoes
WHERE execution_id IS NOT NULL
GROUP BY execution_id
ORDER BY descompactado_em DESC;

-- Resumo por execução: Python (quality_check.py, só volume histórico
-- desde a aposentadoria das checagens de schema/domínio) vs. Glue Data
-- Quality (agora cobre schema, domínio e unicidade -- ver
-- glue/bronze_ingest.py). Como cada execução grava VÁRIAS linhas de
-- glue_data_quality (uma por regra avaliada, formato tidy -- ver
-- _registrar_data_quality), "veredito_glue_dq" agrega: "problema" se
-- QUALQUER regra falhou, senão "ok".
SELECT
  execution_id,
  MIN(CASE WHEN origem = 'quality_check' THEN status END) AS veredito_python,
  MAX(CASE WHEN origem = 'glue_data_quality' AND outcome = 'Failed' THEN 'problema' ELSE NULL END)
    AS veredito_glue_dq
FROM <DATABASE>.controle_execucoes
WHERE execution_id IS NOT NULL
  AND origem IN ('quality_check', 'glue_data_quality')
GROUP BY execution_id
ORDER BY execution_id DESC;

-- Detalhe por regra do Glue Data Quality -- uma linha por regra
-- avaliada, direto filtrável/agrupável sem json_extract (era um blob
-- json numa única coluna "regras" antes desta extensão).
SELECT dt, execution_id, regra, outcome, motivo_falha, metrica_nome, metrica_valor
FROM <DATABASE>.controle_execucoes
WHERE origem = 'glue_data_quality'
ORDER BY dt DESC, execution_id, regra;

-- Quais regras do Glue Data Quality mais falharam, no histórico todo --
-- útil pra priorizar o que calibrar primeiro (limiares, domínio etc.).
SELECT regra,
       COUNT(*) AS total_avaliacoes,
       SUM(CASE WHEN outcome = 'Failed' THEN 1 ELSE 0 END) AS falhas
FROM <DATABASE>.controle_execucoes
WHERE origem = 'glue_data_quality'
GROUP BY regra
ORDER BY falhas DESC;

-- Nota: MSCK REPAIR precisa rodar de novo pra enxergar partições
-- (dt=...) novas -- o mesmo limite que já existia na tabela dtcc_bronze.
-- Rodar isso diariamente (ou antes de consultar) é manual por enquanto.
