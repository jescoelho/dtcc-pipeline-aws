# Pré-leitura obrigatória

Antes de gerar qualquer arquivo, leia `references/parametros.md` e, na
ordem:

1. `README.md` e `DECISOES_MD` — o histórico de decisões e os limites já
   assumidos (ex.: "não há binário `terraform` neste tipo de ambiente").
2. Um contrato existente em `CONTRATOS_DIR` (ex. `dtcc.yaml`) — o contrato de
   referência, com cada campo comentado explicando seu propósito.
3. `MODULO_TERRAFORM/*.tf` — o módulo reutilizável; entender `variables.tf`
   (o que o módulo espera receber) e `locals.tf` (a convenção de
   nomenclatura que todo recurso por fonte segue).
4. `MAIN_TF` — como uma instância do módulo é declarada hoje e com quais
   nomes os recursos compartilhados são passados por referência. **Esta
   leitura é a que confirma ou corrige os defaults da tabela "Recursos
   Terraform compartilhados"** (em `references/parametros.md`) — trate a
   tabela como a última leitura conhecida, não como garantida.
5. `GLUE_SCRIPT_PADRAO` — o job Glue genérico (lê CSV com header, aplica
   ruleset DQDL montado a partir do contrato).
6. Uma Lambda de ingestão agendada existente em `LAMBDA_DIR` (ex.
   `ingerir_cumulative.py`). Seu nome e implementação (`CopyObject` S3→S3)
   são específicos de uma origem que já é um bucket S3 com padrão de nome
   previsível — não generaliza automaticamente para uma URL arbitrária (ver
   passo 3 do procedimento e `references/mecanismo-ingestao.md`).
7. As demais Lambdas genéricas em `LAMBDA_DIR` (checagem de qualidade,
   checagem de pipeline, descompactação, conclusão de job, início de
   pipeline) — dirigidas só por variáveis de ambiente vindas do contrato.
   Estas provavelmente servem para a fonte nova sem alteração.
8. `TESTS_DIR/test_lambda_*.py` — convenção de teste (mocks de boto3 via
   `unittest.mock`, nomes de teste descritivos, docstring em português
   explicando o cenário).
