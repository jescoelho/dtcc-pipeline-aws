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
não feito, ver `docs/DECISOES.md`). Cada execução produz UMA nova instância
do padrão, igual à instância DTCC já existente.

## Como este arquivo está organizado

Esta skill é parametrizável — nada de específico de uma execução (caminho de
arquivo, nome de recurso Terraform, valor numérico de contrato) fica cravado
no meio dos passos abaixo. Esses detalhes vivem em arquivos separados,
carregados só quando o passo correspondente precisar deles, pra manter este
arquivo pequeno mesmo quando a execução é simples:

- **`references/parametros.md`** — tabelas de entrada da execução, caminhos
  do padrão, recursos Terraform compartilhados e defaults de contrato.
  Leia antes do passo 1.
- **`references/mecanismo-ingestao.md`** — os três casos de mecanismo de
  ingestão (S3→S3, HTTP direto, API) e como decidir qual se aplica. Leia no
  passo 3, só depois de já saber qual caso é o da fonte investigada.
- **`assets/modulo-instancia.tf.example`** — template do bloco `module` do
  Terraform. Copie e preencha no passo 6; não é lido antes disso.

Os nomes em `MAIÚSCULAS` abaixo (`CONTRATOS_DIR`, `RECURSO_BUCKET`, etc.) são
os parâmetros definidos em `references/parametros.md`.

## Pré-leitura obrigatória

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
   Terraform compartilhados"** — trate a tabela como a última leitura
   conhecida, não como garantida.
5. `GLUE_SCRIPT_PADRAO` — o job Glue genérico (lê CSV com header, aplica
   ruleset DQDL montado a partir do contrato).
6. Uma Lambda de ingestão agendada existente em `LAMBDA_DIR` (ex.
   `ingerir_cumulative.py`). Seu nome e implementação (`CopyObject` S3→S3)
   são específicos de uma origem que já é um bucket S3 com padrão de nome
   previsível — não generaliza automaticamente para uma URL arbitrária (ver
   passo 3 e `references/mecanismo-ingestao.md`).
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

- Busque `url` (`WebFetch`/`WebSearch` conforme o caso). Determine se é um
  link direto para um arquivo estático, uma API, ou uma página que só
  aponta pro dado real; qual é o provedor real (bucket S3 público, servidor
  HTTP comum, portal de dados); e se existe um padrão de nome de arquivo
  previsível por data ou se o link mais recente precisa ser descoberto.
- Baixe uma amostra real do dado (não apenas metadados/descrição). Se for um
  .zip, descompacte. Se for grande, baixe o suficiente para inspecionar
  cabeçalho/schema e já ter uma ideia de volume diário típico.
- A partir do dado real, levante os campos do contrato (ver passo 5),
  recorrendo aos defaults de `references/parametros.md` apenas quando a
  amostra não permitir inferir algo melhor, e aplicando qualquer override
  recebido como parâmetro de execução antes dos defaults.

### 2. Escolher o nome da fonte

Se `nome` não foi passado como parâmetro de execução, proponha um nome curto,
em minúsculas, sem espaços (mesmo estilo de `dtcc`), a partir da URL/
organização publicadora. Se não for óbvio, pergunte ao usuário (ver "Quando
perguntar").

### 3. Mecanismo de ingestão

Se `pular_ingestao=true`, documente essa opção e siga pro passo 4. Senão,
leia `references/mecanismo-ingestao.md` e decida qual dos três casos
(bucket S3 público, HTTP direto, API/página índice) se aplica à fonte —
nunca force o caso da DTCC (S3→S3) se a investigação do passo 1 mostrou
outra coisa.

### 4. Parsing/Bronze — decidir se o script Glue padrão serve

- Se o novo dado é tabular com cabeçalho (CSV com header, ou um formato que o
  Spark/Glue lê nativamente com schema autodescrito), `GLUE_SCRIPT_PADRAO`
  provavelmente serve só passando os novos argumentos vindos do contrato — a
  lógica de ruleset DQDL já é montada em runtime a partir dos campos de
  schema/identidade/domínio do contrato. Confirme lendo a função de montagem
  do ruleset e veja se algum trecho ainda está cravado para o formato da
  fonte original (ex.: separador, encoding, nome de colunas hardcoded) — se
  estiver, generalize esse trecho específico em vez de duplicar o script
  inteiro, documentando a mudança.
- Se o formato for muito diferente (JSON aninhado, Excel, XML, múltiplos
  arquivos por execução com schemas diferentes), escreva um job Glue novo
  (`GLUE_DIR/bronze_ingest_<nome>.py`, ou generalize o existente com um
  branch por formato) — mas só depois de ter o dado real em mãos.

### 5. Escrever o contrato de fonte

Crie `CONTRATOS_DIR/<nome>.yaml`, cobrindo os mesmos campos do contrato de
referência lido na pré-leitura, e comentando cada suposição não confirmada
contra o comportamento real da fonte (ex.: "ponto de partida, ajustar após
observar algumas semanas"). Campos sem equivalente na nova fonte (ex. sem
coluna de domínio) devem ser omitidos, nunca preenchidos com um valor
arbitrário só para não deixar em branco. Um override recebido como
parâmetro de execução prevalece sobre a inferência automática e sobre os
defaults de `references/parametros.md` — comente no YAML que aquele valor
veio de um override explícito, não da investigação.

### 6. Instanciar o módulo Terraform

Releia `MAIN_TF` primeiro e confirme os nomes reais dos recursos
compartilhados. Depois copie `assets/modulo-instancia.tf.example`, preencha
os placeholders e siga o comentário do próprio template sobre a edição
manual necessária em `DLQ_TF`.

### 7. Escrever os testes

Para toda Lambda nova ou modificada, escreva testes em
`TESTS_DIR/test_lambda_<nome_da_lambda>.py` seguindo a convenção dos testes
existentes: mocks de boto3 via `unittest.mock`, um teste de caminho feliz e
testes de borda relevantes (ex.: fonte ainda não publicada, formato
inesperado), docstrings em português explicando o cenário. Rode `pytest` no
final e confirme que os testes novos e os já existentes continuam passando.

### 8. Validar o Terraform à mão

Este tipo de ambiente normalmente não tem o binário `terraform` instalado
nem acesso de rede pra instalá-lo (limitação já documentada em
`DECISOES_MD`). Revise manualmente o balanceamento de chaves/parênteses, se
toda referência `var.*`/`module.<nome>.*`/`aws_*.*` resolve a algo de fato
declarado, se todo campo referenciado via `var.fonte.*` existe no YAML novo
(e vice-versa), e a profundidade dos `${path.module}/../...` em qualquer
`data.archive_file` nova (confira contra uma Lambda já existente dentro de
`MODULO_TERRAFORM` — não assuma o número de níveis sem conferir).

Diga explicitamente ao usuário que `terraform validate`/`terraform plan`
ainda precisam ser rodados localmente antes de qualquer `apply` — esta
skill não substitui essa etapa.

### 9. Documentar

Atualize `README.md` (se houver seção de fontes/arquitetura) e adicione uma
entrada em `DECISOES_MD` no estilo das entradas existentes: o que foi
assumido sobre a nova fonte, quais decisões foram tomadas (mecanismo de
ingestão, se o job Glue genérico serviu ou precisou de ajuste, quais
parâmetros ficaram no default em vez de inferidos), e quais suposições
ficam marcadas para revisão.

## Quando perguntar ao usuário

Pare e pergunte (não adivinhe, e não caia só no default) quando:

- O nome da fonte não for óbvio e `nome` não foi passado como parâmetro.
- A URL não deixar claro qual arquivo/endpoint é o dado "atual" (várias
  versões, nenhuma marcada como mais recente).
- A origem exigir autenticação/credenciais que o usuário precisa fornecer.
- Faltarem dados reais suficientes para inferir `coluna_id`,
  `unicidade_minima` ou `colunas_obrigatorias` com confiança mínima, e
  nenhum override para esses campos foi passado.
- A decisão envolver algo irreversível ou caro de desfazer no ambiente real
  (ex.: o usuário pedir para já rodar `terraform apply`).

Fora desses casos, tome a decisão mais razoável na ordem de precedência de
`references/parametros.md` (override > inferência > default), documente a
suposição por escrito e sinalize claramente ao usuário o que foi assumido,
em vez de travar o trabalho pedindo confirmação pra cada detalhe.

## Fora de escopo desta skill

- Camadas Silver/Gold (o pipeline hoje só tem Bronze).
- Automatizar a instanciação de múltiplas fontes via `for_each` (passo 3 da
  generalização, deliberadamente não feito — ver `DECISOES_MD`).
- Rodar `terraform apply` de fato, ou qualquer alteração real na conta AWS.
- Consistência sazonal por época do ano (mensal/trimestral/feriados).
