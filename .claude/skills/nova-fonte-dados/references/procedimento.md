# Procedimento detalhado

Leia `references/pre-leitura.md` e `references/parametros.md` antes de
começar. Os nomes em `MAIÚSCULAS` são os parâmetros definidos em
`references/parametros.md`.

## 1. Investigar a URL e os dados reais

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

## 2. Escolher o nome da fonte

Se `nome` não foi passado como parâmetro de execução, proponha um nome curto,
em minúsculas, sem espaços (mesmo estilo de `dtcc`), a partir da URL/
organização publicadora. Se não for óbvio, pergunte ao usuário (ver "Quando
perguntar" em `SKILL.md`).

## 3. Mecanismo de ingestão

Se `pular_ingestao=true`, documente essa opção e siga pro passo 4. Senão,
leia `references/mecanismo-ingestao.md` e decida qual dos três casos
(bucket S3 público, HTTP direto, API/página índice) se aplica à fonte —
nunca force o caso da DTCC (S3→S3) se a investigação do passo 1 mostrou
outra coisa.

## 4. Parsing/Bronze — decidir se o script Glue padrão serve

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

## 5. Escrever o contrato de fonte

Crie `CONTRATOS_DIR/<nome>.yaml`, cobrindo os mesmos campos do contrato de
referência lido na pré-leitura, e comentando cada suposição não confirmada
contra o comportamento real da fonte (ex.: "ponto de partida, ajustar após
observar algumas semanas"). Campos sem equivalente na nova fonte (ex. sem
coluna de domínio) devem ser omitidos, nunca preenchidos com um valor
arbitrário só para não deixar em branco. Um override recebido como
parâmetro de execução prevalece sobre a inferência automática e sobre os
defaults de `references/parametros.md` — comente no YAML que aquele valor
veio de um override explícito, não da investigação.

## 6. Instanciar o módulo Terraform

Releia `MAIN_TF` primeiro e confirme os nomes reais dos recursos
compartilhados. Depois copie `assets/modulo-instancia.tf.example`, preencha
os placeholders e siga o comentário do próprio template sobre a edição
manual necessária em `DLQ_TF`.

## 7. Escrever os testes

Para toda Lambda nova ou modificada, escreva testes em
`TESTS_DIR/test_lambda_<nome_da_lambda>.py` seguindo a convenção dos testes
existentes: mocks de boto3 via `unittest.mock`, um teste de caminho feliz e
testes de borda relevantes (ex.: fonte ainda não publicada, formato
inesperado), docstrings em português explicando o cenário. Rode `pytest` no
final e confirme que os testes novos e os já existentes continuam passando.

## 8. Validar o Terraform à mão

Este tipo de ambiente normalmente não tem o binário `terraform` instalado
nem acesso de rede pra instalá-lo (limitação já documentada em
`DECISOES_MD`). Rode `scripts/validar_terraform.py` contra os arquivos
novos/alterados (`MODULO_TERRAFORM` se for um módulo novo, ou os arquivos
do módulo existente se só a instância mudou), passando `--contrato` com o
YAML da fonte nova:

```
python .claude/skills/nova-fonte-dados/scripts/validar_terraform.py \
  <MODULO_TERRAFORM ou arquivos alterados> --contrato <CONTRATOS_DIR>/<nome>.yaml
```

Ele cobre balanceamento de chaves/parênteses, se toda referência
`var.*`/`module.<nome>.*`/`aws_*.*` resolve a algo declarado, e se todo
campo do YAML é referenciado em `var.fonte.*` (e vice-versa) — a mesma
checagem que já foi feita à mão, com scripts descartáveis, nas mudanças
anteriores deste repositório. É heurística (regex, não um parser HCL
completo) — confira manualmente qualquer problema que ele apontar antes de
corrigir, e confira também a profundidade dos `${path.module}/../...` em
qualquer `data.archive_file` nova (compare contra uma Lambda já existente
dentro de `MODULO_TERRAFORM` — o script não checa isso).

Diga explicitamente ao usuário que `terraform validate`/`terraform plan`
ainda precisam ser rodados localmente antes de qualquer `apply` — nem esta
skill nem o script substituem essa etapa.

## 9. Documentar

Atualize `README.md` (se houver seção de fontes/arquitetura) e adicione uma
entrada em `DECISOES_MD` no estilo das entradas existentes: o que foi
assumido sobre a nova fonte, quais decisões foram tomadas (mecanismo de
ingestão, se o job Glue genérico serviu ou precisou de ajuste, quais
parâmetros ficaram no default em vez de inferidos), e quais suposições
ficam marcadas para revisão.
