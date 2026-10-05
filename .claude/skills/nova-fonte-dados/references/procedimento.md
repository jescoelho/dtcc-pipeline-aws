# Procedimento detalhado

Leia `references/pre-leitura.md` (só a camada 1 agora) e
`references/parametros.md` antes de começar. Os nomes em `MAIÚSCULAS` são os parâmetros definidos em
`references/parametros.md`. `OUT_DIR` = `.claude/outputs/<nome>/`. O
repositório principal é só **referência do modelo**: leia, copie e adapte
para dentro de `OUT_DIR`; nunca o edite. Decisões que cabem ao usuário
vão por `AskUserQuestion` (ver "Quando perguntar" em `SKILL.md`).

## 1. Investigar a URL e os dados reais

Nunca assuma a estrutura dos dados a partir da URL ou do nome do dataset —
este repositório tem o princípio explícito de "validar contra dado real antes
de escrever lógica de parsing" (ver decisões já registradas para a DTCC).

- Comece por `python ${CLAUDE_SKILL_DIR}/scripts/sondar_origem.py <url>` (e consulte
  `references/catalogo.md`): ele já traz `robots.txt`, tipo da URL,
  endpoints candidatos e links de termos. Depois, busque `url`
  (`WebFetch`/`WebSearch`/`curl`) no que faltar. Determine se é um link
  direto para um arquivo estático, uma API, uma SPA que chama uma API
  (leia o JavaScript da página para achar os endpoints), ou uma página que
  só aponta pro dado real; qual é o provedor real (bucket S3 público,
  servidor HTTP comum, portal de dados); e se existe um padrão de nome de
  arquivo previsível por data ou se o link mais recente precisa ser
  descoberto.
- **Leia os termos de uso e o `robots.txt` antes de baixar.** Se
  restringirem copiar/redistribuir ou acesso automatizado, ou exigirem
  aceite, pergunte via `AskUserQuestion` se o usuário tem autorização;
  registre a resposta no contrato e em `DECISOES.md` como declaração do
  usuário, não como verificação. Sem autorização, pare.
- Para a amostra CSV, rode `${CLAUDE_SKILL_DIR}/scripts/perfilar_csv.py` (de preferência com
  vários dias) em vez de analisar à mão.
- **Higiene de contexto:** nunca despeje a amostra inteira na conversa. Use `head`/`perfilar_csv.py` e traga só o resumo (colunas, contagens, 5–10 linhas). Respostas HTML/JS grandes: `grep` pelo que importa.
- Baixe uma amostra real do dado (não apenas metadados/descrição), com
  poucas requisições e User-Agent identificado. Se for um .zip,
  descompacte. Levante: formato, encoding, delimitador, header, nº de
  colunas, preenchimento e unicidade por coluna, domínio das colunas
  categóricas, volume por dia da semana, janela de retenção da origem e
  comportamento em data inexistente/fora da janela.
- A partir do dado real, levante os campos do contrato (ver passo 5),
  recorrendo aos defaults de `references/parametros.md` apenas quando a
  amostra não permitir inferir algo melhor, e aplicando qualquer override
  recebido como parâmetro de execução antes dos defaults.

**Gate:** existe, em texto, a lista de suposições abertas (o que a amostra não permitiu confirmar) e a decisão sobre termos de uso/autorização. Não avance sem amostra real.

## 2. Escolher o nome da fonte

Se `nome` não foi passado como parâmetro de execução, proponha um nome curto,
em minúsculas, sem espaços (mesmo estilo de `dtcc`), a partir da URL/
organização publicadora. Se não for óbvio, pergunte via `AskUserQuestion`.
Crie `OUT_DIR` (pergunte antes se já existir).

**Gate:** `OUT_DIR` criado e nome sem colisão.

## 3. Mecanismo de ingestão

Se `pular_ingestao=true`, documente essa opção e siga pro passo 4. Senão,
leia `references/mecanismo-ingestao.md` e decida qual caso se aplica à
fonte — nunca force o caso da DTCC (S3→S3) se a investigação do passo 1
mostrou outra coisa. Escreva a Lambda de ingestão em `OUT_DIR/lambda/` e o
`.tf` correspondente em `OUT_DIR/terraform/modules/fonte/`.

**Gate:** o caso de mecanismo escolhido está justificado em uma linha com a evidência do passo 1.

## 4. Parsing/Bronze — decidir se o script Glue padrão serve

- Se o novo dado é tabular com cabeçalho (CSV com header, ou um formato que o
  Spark/Glue lê nativamente com schema autodescrito), copie
  `GLUE_SCRIPT_PADRAO` para `OUT_DIR/glue/` — a lógica de ruleset DQDL já é
  montada em runtime a partir dos campos de schema/identidade/domínio do
  contrato. Confira a função de montagem do ruleset e veja se algum trecho
  ainda está cravado para o formato da fonte original (separador, encoding,
  nomes de colunas) — se estiver, generalize esse trecho na cópia.
  Confira também se os nomes de coluna da amostra são aceitos pelo
  Parquet/Spark (` ,;{}()\n\t=` são rejeitados em algumas versões).
- Se o formato for muito diferente (JSON aninhado, Excel, XML, múltiplos
  arquivos por execução com schemas diferentes), escreva um job Glue novo
  (`OUT_DIR/glue/bronze_ingest_<nome>.py`) — mas só depois de ter o dado
  real em mãos.

**Gate:** está decidido (e anotado) se o Glue padrão serve; se não, o motivo é um fato da amostra, não uma preferência.

## 5. Escrever o contrato de fonte

Crie `OUT_DIR/config/fontes/<nome>.yaml`, cobrindo os mesmos campos do
contrato de referência lido na pré-leitura, e comentando cada suposição não
confirmada contra o comportamento real da fonte (ex.: "ponto de partida,
ajustar após observar algumas semanas"). Campos sem equivalente na nova
fonte (ex. sem coluna de domínio) devem ser omitidos, nunca preenchidos com
um valor arbitrário só para não deixar em branco. Um override recebido como
parâmetro de execução prevalece sobre a inferência automática e sobre os
defaults — comente no YAML que aquele valor veio de um override explícito.
Ligue `consistencia_sazonal` só com evidência na amostra (ex.: fim de
semana com volume muito diferente).

**Gate:** todo campo do YAML tem origem rastreável (override, amostra ou default) comentada; nenhum campo preenchido só para não ficar vazio.

## 6. Montar o Terraform autocontido

Em `OUT_DIR/terraform/`:

- Copie `MODULO_TERRAFORM` para `OUT_DIR/terraform/modules/fonte/` e
  adapte: troque a variante de ingestão (o `ingestao_agendada.tf` S3→S3 só
  fica se a origem for S3; senão escreva o `.tf` do mecanismo escolhido,
  com `output "lambda_ingerir"` e `output "ingestao_agendada_rule_arn"`),
  e acerte o `log_retention.tf` e qualquer referência à Lambda de ingestão.
- Copie `MAIN_TF`, `DLQ_TF` e `observabilidade.tf` para `OUT_DIR/terraform/`
  e deixe só o módulo desta fonte (partindo de
  `assets/modulo-instancia.tf.example`), com os ARNs de regra dela na DLQ.
  A raiz continua criando os recursos compartilhados (bucket, IAM do Glue,
  banco/workgroup do Athena, SNS, DLQ, budget) — por isso a pasta é
  aplicável sozinha.
- Copie `LAMBDA_DIR`, `GLUE_DIR` e os scripts/`athena` necessários, e
  adapte o que for específico da DTCC. Cada pipeline é independente (sem
  infraestrutura compartilhada com outros), então a tabela de controle
  `logs/execucoes/` não precisa separar fontes. Oriente o usuário a usar um
  `prefix` próprio por pipeline.
- Gere a tabela Athena da Bronze da fonte (`<nome>_bronze`) em
  `athena/queries.sql` e `scripts/configurar_athena.sh`.

**Gate:** nenhum `${path.module}`/nome de recurso ainda aponta para a DTCC; `OUT_DIR/terraform` tem só o módulo desta fonte.

## 7. Escrever os testes

Em `OUT_DIR/tests/`, copie `pytest.ini`/`requirements-dev.txt` e os
testes das Lambdas genéricas; para toda Lambda nova ou modificada, escreva
`test_lambda_<nome_da_lambda>.py` seguindo a convenção dos testes
existentes: mocks de boto3 (e de HTTP) via `unittest.mock`, um teste de
caminho feliz e testes de borda relevantes (ex.: fonte ainda não publicada,
data fora da janela, resposta que não é o formato esperado, bloqueio 403),
docstrings em português explicando o cenário. Use como fixture um recorte
pequeno (~30 linhas) da amostra real. Rode `python -m pytest` dentro de
`OUT_DIR` e confirme que tudo passa. Se possível, rode a Lambda de
ingestão uma vez contra a origem real com clientes AWS falsos.

**Gate:** `python -m pytest` em `OUT_DIR` verde; se algo falhar, corrija o código/teste — não afrouxe o teste para passar.

## 8. Validar o Terraform à mão

Este tipo de ambiente normalmente não tem o binário `terraform` instalado
nem acesso de rede pra instalá-lo (limitação já documentada em
`DECISOES_MD`). Rode `scripts/validar_terraform.py` contra o módulo e os
arquivos da raiz de `OUT_DIR/terraform` (passe também `observabilidade.tf`,
senão o SNS aparece como não declarado), com `--contrato`:

```
python ${CLAUDE_SKILL_DIR}/scripts/validar_terraform.py \
  .claude/outputs/<nome>/terraform/modules/fonte \
  .claude/outputs/<nome>/terraform/main.tf \
  .claude/outputs/<nome>/terraform/dlq.tf \
  .claude/outputs/<nome>/terraform/observabilidade.tf \
  --contrato .claude/outputs/<nome>/config/fontes/<nome>.yaml
```

Ele cobre balanceamento de chaves/parênteses, se toda referência
`var.*`/`module.<nome>.*`/`aws_*.*` resolve a algo declarado, e se todo
campo do YAML é referenciado em `var.fonte.*` (e vice-versa). É heurística
(regex, não um parser HCL completo) — confira manualmente qualquer problema
que ele apontar antes de corrigir, e confira também a profundidade dos
`${path.module}/../...` em qualquer `data.archive_file` (de
`OUT_DIR/terraform/modules/fonte` são três `..` até `OUT_DIR`).

Diga explicitamente ao usuário que `terraform validate`/`terraform plan`
ainda precisam ser rodados localmente antes de qualquer `apply` — nem esta
skill nem o script substituem essa etapa.

**Gate:** `validar_terraform.py` com código de saída 0, ou cada achado explicado.

## 9. Documentar

Escreva `OUT_DIR/README.md` (estrutura da pasta, fluxo, como testar e
aplicar, condição de uso da fonte) e `OUT_DIR/docs/DECISOES.md` no estilo
das entradas do `DECISOES_MD` de referência: o que a investigação mostrou,
decisões (mecanismo de ingestão, se o Glue genérico serviu ou precisou de
ajuste, o que foi adaptado do modelo), parâmetros no default em vez de
inferidos, e suposições/limites marcados para revisão. Termine a execução
resumindo ao usuário o que foi gerado, o que foi assumido e o que ele
precisa fazer (`terraform validate/plan`, autorização da fonte, riscos).

**Gate:** a "Definição de pronto" do `SKILL.md` está integralmente cumprida.

## 10. Evoluir a skill

Siga `references/evolucao.md`: inventarie o que fez à mão, classifique
(A genérico agora / B esqueleto, aguardar 2ª ocorrência / C específico),
proponha ao usuário (e, só se aprovado, extraia) a classe A para
`scripts/` ou `assets/` com teste de fumaça, e atualize `references/catalogo.md`, a tabela de arquivos do `SKILL.md` e o
registro de mudanças, e diga ao usuário o que a skill aprendeu. Obrigatório,
mesmo que a conclusão seja "nada a extrair".

**Gate:** `${CLAUDE_SKILL_DIR}/scripts/smoke_test.py` passa e o registro de mudanças tem a linha desta execução.

## 8b. Revisão de adequação

Siga `references/revisao-adequacao.md` (brief → juiz → portão humano →
aplicador → novo juiz → `docs/ADEQUACAO.md`). Gate: `achados.json` válido
por rodada, aplicados ⊆ aprovados, `pytest` e `validar_terraform` após a
aplicação, nada alterado fora de `OUT_DIR`.
