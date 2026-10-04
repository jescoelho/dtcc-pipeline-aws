---
name: nova-fonte-dados
description: Use esta skill quando o usuário pedir para aplicar o modelo padrão deste pipeline a uma nova fonte de dados públicos, dado apenas uma URL (ex.: "cria um pipeline pra esta fonte: <url>", "aplica o padrão do repo nesse dataset", "onboarding de fonte nova"). Gera o contrato de fonte, a instância do módulo Terraform, a lógica de ingestão/parsing adaptada e os testes, seguindo exatamente o padrão já estabelecido para a fonte DTCC neste repositório. Parametrizável via `args` (url, nome, overrides de campos do contrato) -- nada de específico de uma execução fica cravado no corpo da skill.
---

# Nova fonte de dados: do URL ao pipeline

## Objetivo

Dado **apenas uma URL** que disponibiliza dados públicos e gratuitos, gerar
automaticamente todas as peças de uma nova instância do pipeline deste
repositório (`dtcc-pipeline-aws`), seguindo o "modelo padrão" já construído e
documentado para a fonte DTCC: arquitetura medallion (Bronze hoje; Silver/Gold
fora de escopo desta skill), contrato de fonte em YAML como fonte única de
verdade, módulo Terraform reutilizável, Lambdas genéricas parametrizadas pelo
contrato, Step Functions, checagens agendadas de qualidade/atualidade/
linhagem, e testes pytest com mocks de boto3.

O usuário não deve precisar responder um questionário detalhado sobre a nova
fonte. A skill investiga a URL e os dados reais para inferir o contrato — e só
pergunta ao usuário quando a resposta certa não pode vir dos dados (ver
"Quando perguntar" abaixo).

**Não generalize demais.** Esta skill replica o padrão para uma fonte nova;
ela não tenta transformar o pipeline num framework multi-fonte genérico
(`for_each`, catálogo de fontes, etc. — isso é o "passo 3", deliberadamente
não feito, ver `docs/DECISOES.md`). Cada execução desta skill produz UMA nova
instância do padrão, igual à instância DTCC já existente.

**Esta skill é ela própria parametrizável** — mesmo princípio aplicado ao
pipeline que ela gera: nenhum caminho de arquivo, nome de recurso ou valor
numérico específico de uma execução fica cravado no meio dos passos. Tudo
isso está declarado na seção "Parâmetros" abaixo, com um default documentado
e a razão do default, para poder ser sobrescrito por execução (via `args` no
`Skill` tool, ex. `nome=cvm unicidade_minima=0.95`) sem editar este arquivo —
e para que o arquivo continue correto se o repositório mudar de estrutura
depois de escrito.

## Parâmetros

### Entrada da execução

| Parâmetro | Obrigatório | Default | Descrição |
|---|---|---|---|
| `url` | sim | — | URL da fonte de dados públicos a investigar. |
| `nome` | não | inferido da URL/organização publicadora durante o passo 2; perguntar ao usuário se ambíguo (ver "Quando perguntar") | Nome curto da fonte (minúsculas, sem espaços). Vira `<CONTRATOS_DIR>/<nome>.yaml` e o nome da instância do módulo Terraform. |
| overrides de qualquer campo do contrato (ex. `dias_historico=14`, `unicidade_minima=0.95`, `consistencia_sazonal=false`) | não | ver "Defaults de contrato" abaixo | Sobrescreve, só para aquele campo, o que o passo 1 inferiria a partir do dado real. Use quando o usuário já souber algo que a skill não teria como inferir sozinha (ex. já conhece a cadência de publicação). |
| `pular_ingestao` | não | `false` | Se `true`, não tenta gerar uma Lambda de ingestão agendada — assume que o usuário vai alimentar a fonte manualmente por enquanto (ver passo 3). |

### Caminhos do padrão

Valores de referência — **releia o repositório antes de confiar neles**, no
início da execução; se algum caminho tiver sido renomeado ou movido desde a
escrita desta skill, use o caminho real e ignore o valor abaixo. Esta tabela
é um atalho de leitura, nunca a fonte de verdade.

| Parâmetro | Default (estado do repositório em 04/10/2026) |
|---|---|
| `CONTRATOS_DIR` | `config/fontes` |
| `MODULO_TERRAFORM` | `terraform/modules/fonte` |
| `MAIN_TF` | `terraform/main.tf` |
| `DLQ_TF` | `terraform/dlq.tf` |
| `GLUE_DIR` | `glue` |
| `LAMBDA_DIR` | `lambda` |
| `TESTS_DIR` | `tests` |
| `DECISOES_MD` | `docs/DECISOES.md` |
| `GLUE_SCRIPT_PADRAO` | `<GLUE_DIR>/bronze_ingest.py` |

### Recursos Terraform compartilhados

O módulo consome estes recursos **por referência** (não os cria). Os nomes
abaixo são os declarados hoje em `MAIN_TF` — releia `MAIN_TF` e confirme os
nomes reais antes de montar o bloco `module` (passo 6); não copie estes
valores as cegas se o arquivo tiver mudado.

| Parâmetro | Default (recurso em `MAIN_TF`, estado em 04/10/2026) |
|---|---|
| `RECURSO_BUCKET` | `aws_s3_bucket.lake` |
| `RECURSO_GLUE_ROLE` | `aws_iam_role.glue` |
| `RECURSO_GLUE_DATABASE` | `aws_glue_catalog_database.lab` |
| `RECURSO_ATHENA_WORKGROUP` | `aws_athena_workgroup.lab` |
| `RECURSO_SNS_TOPIC` | `aws_sns_topic.alertas` |
| `RECURSO_DLQ` | `aws_sqs_queue.eventos_falhos` |

### Defaults de contrato

Usados **só** quando o dado real investigado no passo 1 não permitir inferir
algo mais específico — nunca a primeira escolha quando a amostra real dá
uma resposta melhor.

| Campo do contrato | Default | Quando o default se aplica |
|---|---|---|
| `unicidade_minima` | `0.99` | Sem histórico suficiente pra calibrar contra violações reais — ponto de partida conservador, não uma medição. |
| `dias_historico` | `7` | Sem indício de cadência diferente de "compara contra a última semana". |
| `queda_maxima_tolerada` | `0.5` | Sem indício de quanto o volume da fonte varia naturalmente dia a dia. |
| `consistencia_sazonal` | `false` | Sem evidência, na amostra investigada, de padrão previsível por dia da semana. Ligar exige ter visto o padrão nos dados, não supor que "dado financeiro costuma ter". |
| `semanas_historico_sazonal` | `4` | Só relevante se `consistencia_sazonal: true`. |
| `janela_linhagem_dias` | `3` | Cobre o pior caso de "dia útil anterior" cair numa segunda-feira (3 dias contando o fim de semana). Ajustar só se a fonte publicar com cadência diferente de diária/dia-útil. |
| `margem_linhagem_horas` | `2` | Mesmo valor usado pra DTCC — sem indício de que esta fonte precise de mais ou menos margem até ter histórico real de execução. |

Todo default efetivamente usado (em vez de um valor inferido da amostra
real) deve ser comentado no YAML gerado e citado na entrada de
`DECISOES_MD` — exatamente como os defaults da DTCC já são documentados em
`config/fontes/dtcc.yaml` (ex.: `ingestao_cron` lá é marcado explicitamente
como "ponto de partida, não confirmado").

## Pré-leitura obrigatória

Antes de gerar qualquer arquivo, leia nesta ordem (caminhos conforme a
tabela de parâmetros acima):

1. `README.md` e `DECISOES_MD` — o histórico de decisões e os limites já
   assumidos (ex.: "não há binário `terraform` neste tipo de ambiente").
2. Um contrato existente em `CONTRATOS_DIR` (ex. `dtcc.yaml`) — o contrato de
   referência, com cada campo comentado explicando seu propósito.
3. `MODULO_TERRAFORM/*.tf` — o módulo reutilizável; entender `variables.tf`
   (o que o módulo espera receber) e `locals.tf` (a convenção de
   nomenclatura que todo recurso por fonte segue, hoje
   `"${var.prefix}-${var.fonte.nome}"`).
4. `MAIN_TF` — como uma instância do módulo é declarada hoje e com quais
   nomes os recursos compartilhados (tabela "Recursos Terraform
   compartilhados" acima) são passados por referência. **Esta leitura é a
   que confirma ou corrige os defaults dessa tabela** — trate os valores da
   tabela como a última leitura conhecida, não como garantidos.
5. `GLUE_SCRIPT_PADRAO` — o job Glue genérico (lê CSV com header, aplica
   ruleset DQDL montado a partir do contrato).
6. Uma Lambda de ingestão agendada existente em `LAMBDA_DIR` (ex.
   `ingerir_cumulative.py`). **Importante**: seu nome e implementação
   (`CopyObject` S3→S3) são específicos de uma origem que já é um bucket S3
   com padrão de nome previsível. Isso NÃO generaliza automaticamente para
   uma URL arbitrária (API HTTP, bucket de outro provedor, site com link de
   download). Ver passo 3.
7. As demais Lambdas genéricas em `LAMBDA_DIR` (checagem de qualidade,
   checagem de pipeline, descompactação, conclusão de job, início de
   pipeline) — dirigidas só por variáveis de ambiente vindas do contrato.
   Estas provavelmente servem para a fonte nova sem alteração.
8. `TESTS_DIR/test_lambda_*.py` — convenção de teste (mocks de boto3 via
   `unittest.mock`, nomes de teste descritivos, docstring em português
   explicando o cenário).

## Procedimento

### 1. Investigar a URL e os dados reais

Nunca assuma a estrutura dos dados a partir da URL ou do nome do dataset —
este repositório tem o princípio explícito de "validar contra dado real antes
de escrever lógica de parsing" (ver decisões já registradas para a DTCC).

- Busque `url` (`WebFetch`/`WebSearch` conforme o caso). Determine:
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
- A partir do dado real, levante os campos do contrato (ver passo 5),
  recorrendo aos defaults da tabela "Defaults de contrato" apenas quando a
  amostra não permitir inferir algo melhor, e aplicando qualquer override
  recebido como parâmetro de execução antes dos defaults.

### 2. Escolher o nome da fonte

Se `nome` não foi passado como parâmetro de execução, proponha um nome curto,
em minúsculas, sem espaços (mesmo estilo de `dtcc`), a partir da URL/
organização publicadora. Se não for óbvio, pergunte ao usuário (ver "Quando
perguntar").

### 3. Mecanismo de ingestão — decidir, não assumir

Se `pular_ingestao=true`, documente essa opção no YAML/`DECISOES_MD` e pule
para o passo 4 — o usuário vai alimentar a fonte manualmente por enquanto.

Senão, o padrão hoje (Lambda de ingestão lida em "Pré-leitura", item 6)
assume uma origem que é um bucket S3 público com nome de arquivo previsível,
copiado via `CopyObject` (cópia servidor-a-servidor, nunca passa pela
Lambda). Isso só se aplica quando a origem real é de fato um bucket S3 (de
qualquer conta/provedor que suporte acesso público). Decida qual dos casos
se aplica e implemente de acordo — nunca force o caso S3→S3 se a origem é
outra:

- **Origem é outro bucket S3 público, nome de arquivo previsível por data**:
  reaproveite o padrão exato da Lambda de referência — crie
  `LAMBDA_DIR/ingerir_<nome>.py` como uma cópia adaptada (variáveis de
  ambiente equivalentes: origem, prefixo de destino, bucket do laboratório,
  tópico SNS, nome da fonte, mais o que for específico desta fonte).
- **Origem é um link HTTP direto (não S3), arquivo estático por data ou
  fixo**: escreva uma Lambda nova que baixa via HTTP (`urllib3`/`requests`,
  já disponível no runtime do Lambda ou embutido no pacote da função) e grava
  no bucket do laboratório via `s3.put_object` — mesma estrutura de
  agendamento (EventBridge Schedule), mesmo padrão de alerta (publica no
  tópico SNS compartilhado em caso de falha, porque também aqui não existe
  evento nativo da AWS para "download HTTP falhou").
- **Origem é uma API paginada/autenticada, ou exige descoberta de link (ex.:
  "link do dia" só aparece numa página índice)**: ainda assim, uma Lambda
  agendada que faz a requisição necessária e grava o resultado no bucket —
  mas documente explicitamente a complexidade adicional (paginação,
  autenticação, parsing de página índice) no cabeçalho do arquivo e em
  `DECISOES_MD`, do mesmo jeito que o código existente documenta cada
  decisão não trivial.

### 4. Parsing/Bronze — decidir se o script Glue padrão serve

- Se o novo dado é tabular com cabeçalho (CSV com header, ou um formato que o
  Spark/Glue lê nativamente com schema autodescrito), `GLUE_SCRIPT_PADRAO`
  provavelmente serve só passando os novos argumentos vindos do contrato — a
  lógica de ruleset DQDL já é montada em runtime a partir dos campos de
  schema/identidade/domínio do contrato. Confirme lendo a função de montagem
  do ruleset em `GLUE_SCRIPT_PADRAO` e veja se algum trecho ainda está
  cravado para o formato da fonte original (ex.: separador, encoding, nome
  de colunas hardcoded) — se estiver, generalize esse trecho específico em
  vez de duplicar o script inteiro, documentando a mudança.
- Se o formato for muito diferente (JSON aninhado, Excel, XML, múltiplos
  arquivos por execução com schemas diferentes), escreva um job Glue novo
  (`GLUE_DIR/bronze_ingest_<nome>.py`, ou generalize o existente com um
  branch por formato) — mas só depois de ter o dado real em mãos; não
  desenhe o parsing a partir da descrição da fonte.

### 5. Escrever o contrato de fonte

Crie `CONTRATOS_DIR/<nome>.yaml`, cobrindo os mesmos campos do contrato de
referência lido na pré-leitura, e comentando cada suposição não confirmada
contra o comportamento real da fonte, no mesmo estilo (ex.: "ponto de
partida, ajustar após observar algumas semanas"). Campos sem equivalente na
nova fonte (ex. sem coluna de domínio) devem ser omitidos, nunca preenchidos
com um valor arbitrário só para não deixar em branco. Qualquer override
recebido como parâmetro de execução (ver "Entrada da execução") prevalece
sobre a inferência automática e sobre os defaults da tabela "Defaults de
contrato" — comente no YAML que aquele valor veio de um override explícito,
não da investigação.

### 6. Instanciar o módulo Terraform

Releia `MAIN_TF` primeiro e confirme os nomes reais dos recursos
compartilhados (tabela "Recursos Terraform compartilhados") — o bloco abaixo
usa os parâmetros declarados nesta skill, não valores fixos:

```hcl
locals {
  fonte_<nome> = yamldecode(file("${path.module}/../<CONTRATOS_DIR>/<nome>.yaml"))
}

module "<nome>" {
  source = "./modules/fonte"   # valor de MODULO_TERRAFORM, relativo a MAIN_TF

  fonte                      = local.fonte_<nome>
  prefix                     = var.prefix
  region                     = var.region
  account_id                 = data.aws_caller_identity.me.account_id
  bucket_name                = <RECURSO_BUCKET>.id
  bucket_arn                 = <RECURSO_BUCKET>.arn
  glue_role_arn              = <RECURSO_GLUE_ROLE>.arn
  glue_catalog_database_name = <RECURSO_GLUE_DATABASE>.name
  glue_catalog_database_arn  = <RECURSO_GLUE_DATABASE>.arn
  athena_workgroup_name      = <RECURSO_ATHENA_WORKGROUP>.name
  athena_workgroup_arn       = <RECURSO_ATHENA_WORKGROUP>.arn
  sns_topic_arn              = <RECURSO_SNS_TOPIC>.arn
  dlq_arn                    = <RECURSO_DLQ>.arn
  glue_script_source_path    = "${path.module}/../<script_bronze_desta_fonte>"
}
```

Siga exatamente o padrão da instância existente em `MAIN_TF` — mesmos
recursos compartilhados passados por referência, nada duplicado.

`DLQ_TF` precisa de atenção manual: a política da fila DLQ hoje lista
explicitamente os ARNs de regra do EventBridge de cada fonte (ver comentário
em `DECISOES_MD` sobre o passo 3 não feito). Adicione as 4 ARNs equivalentes
da nova fonte (`module.<nome>.zip_arrived_rule_arn`,
`module.<nome>.glue_bronze_concluido_rule_arn`,
`module.<nome>.checar_pipeline_agenda_rule_arn`,
`module.<nome>.ingestao_agendada_rule_arn`) à lista — não crie uma fila DLQ
nova por fonte.

### 7. Escrever os testes

Para toda Lambda nova ou modificada (ingestão adaptada, Glue script
adaptado), escreva testes em `TESTS_DIR/test_lambda_<nome_da_lambda>.py`
seguindo exatamente a convenção dos testes existentes: mocks de boto3 via
`unittest.mock`, um teste de caminho feliz e testes de borda relevantes
(ex.: fonte ainda não publicada, formato inesperado), docstrings em
português explicando o cenário. Rode `pytest` no final e confirme que os
testes novos E os já existentes continuam passando.

### 8. Validar o Terraform à mão

Este tipo de ambiente normalmente não tem o binário `terraform` instalado
nem acesso de rede para instalá-lo (limitação já documentada em
`DECISOES_MD`). Revise manualmente:

- Balanceamento de chaves/parênteses nos arquivos `.tf` tocados.
- Toda referência `var.*`, `module.<nome>.*`, `aws_*.*` resolve a algo
  de fato declarado.
- Todo campo referenciado via `var.fonte.*` dentro do módulo existe no YAML
  novo (e vice-versa — nenhum campo do YAML fica sem uso).
- Profundidade dos `${path.module}/../...` em qualquer `data.archive_file`
  nova (confira a profundidade real comparando com uma Lambda já existente
  dentro de `MODULO_TERRAFORM` — não assuma o número de níveis sem
  conferir).

Diga explicitamente ao usuário, no mesmo tom já usado nos commits deste
repositório, que `terraform validate`/`terraform plan` ainda precisam ser
rodados localmente antes de qualquer `apply` — esta skill não substitui essa
etapa.

### 9. Documentar

- Atualize `README.md` (seção de fontes disponíveis/arquitetura, se houver).
- Adicione uma entrada em `DECISOES_MD` no mesmo estilo das entradas
  existentes: o que foi assumido sobre a nova fonte, quais decisões foram
  tomadas (mecanismo de ingestão, se o job Glue genérico serviu ou precisou
  de ajuste, quais parâmetros ficaram no default em vez de inferidos), e
  quais suposições ficam marcadas para revisão.

## Quando perguntar ao usuário

Pare e pergunte (não adivinhe, e não caia só no default) quando:

- O nome da fonte não for óbvio a partir da URL/organização, e `nome` não
  foi passado como parâmetro de execução.
- A URL não deixar claro qual arquivo/endpoint é o dado "atual" (várias
  versões, nenhuma marcada como mais recente) — confirmar qual delas usar
  é uma decisão, não uma suposição segura.
- A origem exigir autenticação/credenciais que o usuário precisa fornecer.
- Faltarem dados reais suficientes para inferir `coluna_id`,
  `unicidade_minima` ou `colunas_obrigatorias` com confiança mínima, e
  nenhum override para esses campos foi passado como parâmetro — é melhor
  perguntar o que o usuário já sabe sobre a fonte do que aplicar o default
  silenciosamente para um campo que determina a corretude da checagem de
  qualidade.
- A decisão envolver algo irreversível ou caro de desfazer no ambiente real
  (ex.: o usuário pedir para já rodar `terraform apply`) — esta skill só
  gera o código; aplicar fica sempre a critério explícito do usuário.

Fora desses casos, siga o princípio já usado neste repositório: tome a
decisão mais razoável (override passado > inferência do dado real > default
da tabela, nessa ordem), documente a suposição por escrito (contrato YAML,
`DECISOES_MD`, docstring do código) e sinalize claramente ao usuário o que
foi assumido, em vez de travar o trabalho pedindo confirmação para cada
detalhe.

## Fora de escopo desta skill

- Camadas Silver/Gold (o pipeline hoje só tem Bronze).
- Automatizar a instanciação de múltiplas fontes via `for_each`
  (passo 3 da generalização, deliberadamente não feito — ver
  `DECISOES_MD`).
- Rodar `terraform apply` de fato, ou qualquer alteração real na conta AWS.
- Consistência sazonal por época do ano (mensal/trimestral/feriados) — fora
  de escopo também para a fonte original, por falta de histórico para
  calibrar.
