# Pré-leitura — em camadas (orçamento de contexto)

Tudo abaixo é lido do **repositório principal como modelo de referência**
(somente leitura); o que for gerado vai para `OUT_DIR`
(`.claude/outputs/<nome>/`). O repositório tem milhares de linhas
(`docs/DECISOES.md` sozinho passa de mil): **não leia tudo de uma vez**.
Carregue só o que o passo corrente exige e prefira `Grep`/`Read` com
`offset`/`limit` a abrir arquivos inteiros.

Antes de tudo: `references/catalogo.md` (o que já existe para reutilizar) e
`references/parametros.md`. Se já houver pipelines em `.claude/outputs/`,
use o mais parecido como exemplo concluído (ex. `ice_ticker/`, API HTTP com
token) em vez de reler o modelo DTCC.

## Camada 1 — antes do passo 1 (essencial, ~400 linhas)

1. Um contrato em `CONTRATOS_DIR` (ex. `dtcc.yaml`) — cada campo comentado.
2. `MODULO_TERRAFORM/variables.tf` e `locals.tf` — o que o módulo espera
   receber e a convenção de nomes.
3. `MAIN_TF` — só o bloco que instancia o módulo e os recursos
   compartilhados. **Confirma ou corrige a tabela "Recursos Terraform
   compartilhados" de `parametros.md`** (trate-a como última leitura
   conhecida, não como garantida).

## Camada 2 — sob demanda, no passo que precisar

| Passo | Leia |
|---|---|
| 3 | A Lambda de ingestão de referência (ex. `ingerir_cumulative.py`) e seu teste — específica de origem S3; ver `mecanismo-ingestao.md` antes de copiar |
| 4 | `GLUE_SCRIPT_PADRAO` (função de montagem do ruleset DQDL) |
| 6 | O restante de `MODULO_TERRAFORM/*.tf` (só os arquivos que for adaptar) e `DLQ_TF` |
| 7 | Um `TESTS_DIR/test_lambda_*.py` como convenção (mocks de boto3 via `unittest.mock`, docstring em português) |
| 9 | `README.md` (estrutura) e as **últimas** entradas de `DECISOES_MD` (estilo); use `Grep '^## '` para achar a seção certa, não leia o arquivo inteiro |

As Lambdas genéricas (qualidade, checagem de pipeline, descompactação,
conclusão de job, início) são dirigidas só por variáveis de ambiente do
contrato e provavelmente servem sem alteração: copie-as sem lê-las; só abra
uma se o teste falhar ou o formato da fonte colidir com ela.
