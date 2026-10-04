---
name: nova-fonte-dados
description: Use esta skill quando o usuário pedir para aplicar o modelo padrão deste pipeline a uma nova fonte de dados públicos, dado apenas uma URL (ex.: "cria um pipeline pra esta fonte: <url>", "aplica o padrão do repo nesse dataset", "onboarding de fonte nova"). Gera o contrato de fonte, a instância do módulo Terraform, a lógica de ingestão/parsing adaptada e os testes, seguindo exatamente o padrão já estabelecido para a fonte DTCC neste repositório.
---

# Nova fonte de dados: do URL ao pipeline

## Objetivo

Dado **apenas uma URL** que disponibiliza dados públicos e gratuitos, gerar
automaticamente todas as peças de uma nova instância do pipeline deste
repositório (`dtcc-pipeline-aws`), seguindo o "modelo padrão" já construído e
documentado para a fonte DTCC: arquitetura medallion (Bronze hoje; Silver/Gold
fora de escopo desta skill), contrato de fonte em YAML como fonte única de
verdade, módulo Terraform reutilizável (`terraform/modules/fonte`), Lambdas
genéricas parametrizadas pelo contrato, Step Functions, checagens agendadas de
qualidade/atualidade/linhagem, e testes pytest com mocks de boto3.

O usuário não deve precisar responder um questionário detalhado sobre a nova
fonte. A skill investiga a URL e os dados reais para inferir o contrato — e só
pergunta ao usuário quando a resposta certa não pode vir dos dados (ver
"Quando perguntar" abaixo).

**Não generalize demais.** Esta skill replica o padrão para uma fonte nova;
ela não tenta transformar o pipeline num framework multi-fonte genérico
(`for_each`, catálogo de fontes, etc. — isso é o "passo 3", deliberadamente
não feito, ver `docs/DECISOES.md`). Cada execução desta skill produz UMA nova
instância do padrão, igual à instância DTCC já existente.

## Pré-leitura obrigatória

Antes de gerar qualquer arquivo, leia nesta ordem:

1. `README.md` e `docs/DECISOES.md` — o histórico de decisões e os limites já
   assumidos (ex.: "não há binário `terraform` neste tipo de ambiente").
2. `config/fontes/dtcc.yaml` — o contrato de referência, com cada campo
   comentado explicando seu propósito.
3. `terraform/modules/fonte/*.tf` — o módulo reutilizável; entender
   `variables.tf` (o que o módulo espera receber) e `locals.tf`
   (`local.nome = "${var.prefix}-${var.fonte.nome}"`, a convenção de
   nomenclatura que TODO recurso por fonte segue).
4. `terraform/main.tf` — como a instância `module "dtcc" { ... }` é declarada
   e quais recursos compartilhados (bucket, banco Glue, workgroup Athena,
   tópico SNS, fila DLQ, role do Glue) ela recebe por referência.
5. `glue/bronze_ingest.py` — o job Glue genérico (lê CSV com header, aplica
   ruleset DQDL montado a partir do contrato).
6. `lambda/ingerir_cumulative.py` — a Lambda de ingestão agendada da DTCC.
   **Importante**: o nome do arquivo e a implementação (`CopyObject` S3→S3)
   são específicos de uma origem que já é um bucket S3 com padrão de nome
   previsível. Isso NÃO generaliza automaticamente para uma URL arbitrária
   (API HTTP, bucket de outro provedor, site com link de download). Ver
   "Mecanismo de ingestão" abaixo.
7. `lambda/quality_check.py`, `lambda/checar_pipeline.py`, `lambda/unzip_dtcc.py`,
   `lambda/job_concluido.py`, `lambda/iniciar_pipeline.py` — as Lambdas
   genéricas que o módulo reutiliza sem alteração (dirigidas só por variáveis
   de ambiente vindas do contrato). Estas provavelmente servem para a fonte
   nova sem modificação.
8. `tests/test_lambda_*.py` — convenção de teste (mocks de boto3 via
   `unittest.mock`, nomes de teste descritivos, docstring em português
   explicando o cenário).

## Procedimento

### 1. Investigar a URL e os dados reais

Nunca assuma a estrutura dos dados a partir da URL ou do nome do dataset —
este repositório tem o princípio explícito de "validar contra dado real antes
de escrever lógica de parsing" (ver decisões já registradas para a DTCC).

- Busque a URL (`WebFetch`/`WebSearch` conforme o caso). Determine:
  - É um link direto para um arquivo estático (CSV, JSON, XML, Excel, .zip),
    uma API (JSON/REST, paginada ou não), ou uma página HTML que só contém um
    link mais profundo para o dado real?
  - Qual é o provedor real do dado: um bucket S3 público (ou equivalente de
    outro provedor de nuvem), um servidor HTTP comum, um portal de dados
    (data.gov, Banco Central, CVM, SEC EDGAR, etc.)?
  - Existe um padrão de nome de arquivo previsível por data (como o
    `origem_padrao` da DTCC), ou o link mais recente precisa ser descoberto
    via uma página índice?
- Baixe uma amostra real do dado (não apenas metadados/descrição) para uma
  pasta de trabalho temporária. Se for um .zip, descompacte. Se for grande,
  baixe o suficiente para inspecionar cabeçalho/schema e já ter uma ideia de
  volume diário típico.
- A partir do dado real, levante:
  - Formato de arquivo e se tem cabeçalho/schema autodescrito (como o
    Cumulative, que o Glue já lê via header) ou exige schema explícito.
  - Colunas obrigatórias plausíveis (`colunas_obrigatorias`).
  - Uma coluna que identifica univocamente um registro (`coluna_id`) e uma
    estimativa realista de unicidade (`unicidade_minima` — comece
    conservador, ex. `0.99`, e documente que é um ponto de partida sem
    histórico real, do mesmo jeito que o contrato da DTCC documenta
    `ingestao_cron` como "não confirmado contra o comportamento real").
  - Uma coluna categórica de domínio limitado, se existir
    (`coluna_dominio`/`valores_dominio`) — nem toda fonte tem uma; omita os
    dois campos do contrato se não houver (confira em `glue/bronze_ingest.py`
    se o ruleset já trata a ausência desses campos com uma condicional, e se
    não tratar, ajuste o script genérico para tratar — não crave uma regra
    fictícia só para preencher o campo).
  - Cadência de atualização aparente (diária, intradiária, semanal) — ajuda a
    escolher `checagem_cron`, `ingestao_cron`, `dias_historico`,
    `janela_linhagem_dias`, `margem_linhagem_horas` por analogia ao raciocínio
    já documentado para a DTCC (ex.: pular fim de semana se a fonte não
    publica nesses dias).
  - Se o volume variar previsivelmente por dia da semana (ex.: segunda após
    fim de semana) — se sim, `consistencia_sazonal: true` com
    `semanas_historico_sazonal` plausível; se não há indício ou não há como
    saber ainda, `consistencia_sazonal: false` é a opção honesta (documente
    por quê, como o contrato da DTCC documenta cada suposição).

### 2. Escolher o nome da fonte

Proponha um `nome` curto, em minúsculas, sem espaços (mesmo estilo de `dtcc`).
Se não for óbvio a partir da URL/organização publicadora, pergunte ao usuário
(ver "Quando perguntar").

### 3. Mecanismo de ingestão — decidir, não assumir

O padrão hoje (`lambda/ingerir_cumulative.py`) assume uma origem que é um
bucket S3 público com nome de arquivo previsível, copiado via `CopyObject`
(cópia servidor-a-servidor, nunca passa pela Lambda). Isso só se aplica
quando a origem real é de fato um bucket S3 (de qualquer conta/provedor que
suporte acesso público). Para esta nova fonte, decida qual dos casos se
aplica e implemente de acordo — nunca force o caso S3→S3 se a origem é outra:

- **Origem é outro bucket S3 público, nome de arquivo previsível por data**:
  reaproveite o padrão exato de `lambda/ingerir_cumulative.py` — crie
  `lambda/ingerir_<nome>.py` como uma cópia adaptada (variáveis de ambiente
  equivalentes: `ORIGEM_BUCKET`, `ORIGEM_PADRAO`, `ZIP_PREFIX` ou
  `RAW_PREFIX` conforme o formato, `BUCKET`, `SNS_TOPIC_ARN`, `NOME_FONTE`,
  mais o que for específico desta fonte).
- **Origem é um link HTTP direto (não S3), arquivo estático por data ou
  fixo**: escreva uma Lambda nova que baixa via HTTP (`urllib3`/`requests`,
  já disponível no runtime do Lambda ou embutido no pacote da função) e grava
  no bucket do laboratório via `s3.put_object` — mesma estrutura de
  agendamento (EventBridge Schedule), mesmo padrão de alerta (publica no
  tópico SNS compartilhado em caso de falha, igual a
  `lambda/ingerir_cumulative.py`, porque também aqui não existe evento nativo
  da AWS para "download HTTP falhou").
- **Origem é uma API paginada/autenticada, ou exige descoberta de link (ex.:
  "link do dia" só aparece numa página índice)**: ainda assim, uma Lambda
  agendada que faz a requisição necessária e grava o resultado no bucket —
  mas documente explicitamente a complexidade adicional (paginação,
  autenticação, parsing de página índice) no cabeçalho do arquivo e em
  `docs/DECISOES.md`, do mesmo jeito que o código existente documenta cada
  decisão não trivial.
- Em qualquer caso, a Lambda de ingestão é **opcional** na primeira versão:
  se o usuário for alimentar a fonte manualmente a princípio (upload do
  arquivo direto no prefixo `zip_prefix`/`raw_prefix`), está tudo bem deixar
  a automação de ingestão para depois — mas diga isso explicitamente ao
  usuário, não omita a lacuna silenciosamente.

### 4. Parsing/Bronze — decidir se `glue/bronze_ingest.py` serve

- Se o novo dado é tabular com cabeçalho (CSV com header, ou um formato que o
  Spark/Glue lê nativamente com schema autodescrito), o job genérico
  provavelmente serve só passando os novos argumentos vindos do contrato — a
  lógica de ruleset DQDL já é montada em runtime a partir de
  `colunas_obrigatorias`/`coluna_id`/`unicidade_minima`/`coluna_dominio`/`valores_dominio`.
  Confirme lendo `_montar_ruleset` (ou equivalente) em `glue/bronze_ingest.py`
  e veja se algum trecho ainda está cravado para o formato do Cumulative (ex.:
  separador, encoding, nome de colunas hardcoded) — se estiver, generalize
  esse trecho específico em vez de duplicar o script inteiro, documentando a
  mudança.
- Se o formato for muito diferente (JSON aninhado, Excel, XML, múltiplos
  arquivos por execução com schemas diferentes), escreva um job Glue novo
  (`glue/bronze_ingest_<nome>.py` ou generalize o existente com um branch por
  formato) — mas só depois de ter o dado real em mãos; não desenhe o parsing
  a partir da descrição da fonte.

### 5. Escrever o contrato de fonte

Crie `config/fontes/<nome>.yaml`, cobrindo exatamente os mesmos campos de
`config/fontes/dtcc.yaml` (veja a lista completa nesse arquivo) e comentando
cada suposição não confirmada contra o comportamento real da fonte, no mesmo
estilo (ex.: "ponto de partida, ajustar após observar algumas semanas").
Campos sem equivalente na nova fonte (ex. sem coluna de domínio) devem ser
omitidos, nunca preenchidos com um valor arbitrário só para não deixar em
branco.

### 6. Instanciar o módulo Terraform

Em `terraform/main.tf`, adicione:

```hcl
locals {
  fonte_<nome> = yamldecode(file("${path.module}/../config/fontes/<nome>.yaml"))
}

module "<nome>" {
  source = "./modules/fonte"

  fonte                      = local.fonte_<nome>
  prefix                     = var.prefix
  region                     = var.region
  account_id                 = data.aws_caller_identity.me.account_id
  bucket_name                = aws_s3_bucket.lake.id
  bucket_arn                 = aws_s3_bucket.lake.arn
  glue_role_arn              = aws_iam_role.glue.arn
  glue_catalog_database_name = aws_glue_catalog_database.lab.name
  glue_catalog_database_arn  = aws_glue_catalog_database.lab.arn
  athena_workgroup_name      = aws_athena_workgroup.lab.name
  athena_workgroup_arn       = aws_athena_workgroup.lab.arn
  sns_topic_arn              = aws_sns_topic.alertas.arn
  dlq_arn                    = aws_sqs_queue.eventos_falhos.arn
  glue_script_source_path    = "${path.module}/../glue/<script_bronze_desta_fonte>.py"
}
```

Siga exatamente o padrão do bloco `module "dtcc"` já existente — mesmos
recursos compartilhados passados por referência, nada duplicado.

**`terraform/dlq.tf`** precisa de atenção manual: a política da fila DLQ
hoje lista explicitamente os ARNs de regra do EventBridge de cada fonte
(`module.dtcc.zip_arrived_rule_arn`, etc. — ver comentário em
`docs/DECISOES.md` sobre o passo 3 não feito). Adicione as 4 ARNs
equivalentes da nova fonte (`module.<nome>.zip_arrived_rule_arn`,
`module.<nome>.glue_bronze_concluido_rule_arn`,
`module.<nome>.checar_pipeline_agenda_rule_arn`,
`module.<nome>.ingestao_agendada_rule_arn`) à lista — não crie uma fila DLQ
nova por fonte.

### 7. Escrever os testes

Para toda Lambda nova ou modificada (ingestão adaptada, Glue script
adaptado), escreva testes em `tests/test_lambda_<nome_da_lambda>.py`
seguindo exatamente a convenção dos testes existentes: mocks de boto3 via
`unittest.mock`, um teste de caminho feliz e testes de borda relevantes
(ex.: fonte ainda não publicada, formato inesperado), docstrings em
português explicando o cenário. Rode `pytest` no final e confirme que os
testes novos E os já existentes (41 ao escrever esta skill) continuam
passando.

### 8. Validar o Terraform à mão

Este tipo de ambiente normalmente não tem o binário `terraform` instalado
nem acesso de rede para instalá-lo (limitação já documentada em
`docs/DECISOES.md`). Revise manualmente:

- Balanceamento de chaves/parênteses nos arquivos `.tf` tocados.
- Toda referência `var.*`, `module.<nome>.*`, `aws_*.*` resolve a algo
  de fato declarado.
- Todo campo referenciado via `var.fonte.*` dentro do módulo existe no YAML
  novo (e vice-versa — nenhum campo do YAML fica sem uso).
- Profundidade dos `${path.module}/../...` em qualquer `data.archive_file`
  nova (3 níveis até a raiz do repo para arquivos-fonte, 2 níveis até
  `terraform/.build/` para o zip de saída — mesma contagem usada no módulo
  `fonte` existente).

Diga explicitamente ao usuário, no mesmo tom já usado nos commits deste
repositório, que `terraform validate`/`terraform plan` ainda precisam ser
rodados localmente antes de qualquer `apply` — esta skill não substitui essa
etapa.

### 9. Documentar

- Atualize `README.md` (seção de fontes disponíveis/arquitetura, se houver).
- Adicione uma entrada em `docs/DECISOES.md` no mesmo estilo das entradas
  existentes: o que foi assumido sobre a nova fonte, quais decisões foram
  tomadas (mecanismo de ingestão, se o job Glue genérico serviu ou precisou
  de ajuste), e quais suposições ficam marcadas para revisão (cron sem
  confirmação contra o comportamento real, unicidade mínima sem histórico,
  etc.).

## Quando perguntar ao usuário

Pare e pergunte (não adivinhe) quando:

- O nome da fonte não for óbvio a partir da URL/organização.
- A URL não deixar claro qual arquivo/endpoint é o dado "atual" (várias
  versões, nenhuma marcada como mais recente) — confirmar qual delas usar
  é uma decisão, não uma suposição segura.
- A origem exigir autenticação/credenciais que o usuário precisa fornecer.
- Faltarem dados reais suficientes para inferir `coluna_id`,
  `unicidade_minima` ou `colunas_obrigatorias` com confiança mínima — é
  melhor perguntar o que o usuário já sabe sobre a fonte do que inventar um
  valor sem base.
- A decisão envolver algo irreversível ou caro de desfazer no ambiente real
  (ex.: o usuário pedir para já rodar `terraform apply`) — esta skill só
  gera o código; aplicar fica sempre a critério explícito do usuário.

Fora desses casos, siga o princípio já usado neste repositório: tome a
decisão mais razoável, documente a suposição por escrito (contrato YAML,
`docs/DECISOES.md`, docstring do código) e sinalize claramente ao usuário o
que foi assumido, em vez de travar o trabalho pedindo confirmação para cada
detalhe.

## Fora de escopo desta skill

- Camadas Silver/Gold (o pipeline hoje só tem Bronze).
- Automatizar a instanciação de múltiplas fontes via `for_each`
  (passo 3 da generalização, deliberadamente não feito — ver
  `docs/DECISOES.md`).
- Rodar `terraform apply` de fato, ou qualquer alteração real na conta AWS.
- Consistência sazonal por época do ano (mensal/trimestral/feriados) — fora
  de escopo também para a fonte original, por falta de histórico para
  calibrar.
