# Pipeline DTCC PPD na AWS — laboratório de dado tempestivo

Projeto irmão do [`b3-pipeline-aws`](https://github.com/jescoelho/b3-pipeline-aws),
focado em um tipo de dado diferente: em vez de um arquivo diário imutável
(COTAHIST), aqui a fonte é um **feed de eventos** — transações de swap
publicadas pelo **PPD (Public Price Dissemination) do DTCC**, onde cada
linha pode se referir e alterar o estado de uma transação anterior. É o
caso real que motivou o laboratório de CDC (Change Data Capture) — este
repositório aplica aquele conceito contra dado de verdade.

Fonte: https://pddata.dtcc.com/ppd/cftcdashboard — relatório **Cumulative**,
classe de ativo **Rates** (a mais relevante para Tesouraria/Risco).

```
DTCC PPD Cumulative (.csv, ~110 colunas)
  -> S3 raw/
  -> Glue Bronze  -> S3 bronze/  (linha crua, sem tipar, com proveniência)   [feito, local + AWS]
  -> Glue Silver  -> S3 silver/  (cadeia de eventos resolvida -> estado atual por swap)  [a seguir]
  -> Glue Gold    -> S3 gold/    (métricas de negócio, a definir)
  -> Athena (consulta direto na camada disponível)
```

Segue a mesma **arquitetura medalhão** (Bronze/Silver/Gold) do
`b3-pipeline-aws`, pelos mesmos motivos: cada camada é reprocessável
independente das outras.

**Por que o Cumulative não é um snapshot** — cada linha é um evento de
disseminação (`Action type`: `NEWT`/`MODI`/`CORR`/`TERM`/`EROR`/`REVI`),
não uma transação definitiva; reconstruir o estado atual de uma
transação exige processar a cadeia de eventos na ordem do tempo. Ver
[`docs/ESTUDO.md`](docs/ESTUDO.md) para a explicação completa, em
linguagem simples e sem assumir conhecimento prévio.

## Documentação

| Arquivo | Conteúdo |
|---|---|
| `README.md` (este arquivo) | Visão geral, como rodar, estado atual, próximos passos |
| [`docs/ESTUDO.md`](docs/ESTUDO.md) | O domínio do dado explicado do zero — por que o Cumulative é um log de eventos, não um snapshot |
| [`docs/DECISOES.md`](docs/DECISOES.md) | Changelog narrativo de arquitetura — cada decisão, bug real encontrado e extensão de observabilidade, em ordem cronológica, com o raciocínio e os limites assumidos |
| [`athena/queries.sql`](athena/queries.sql) | Queries de exemplo para explorar `dtcc_bronze` e `controle_execucoes` |

## Como rodar

### Fase 0 — local, sem AWS (feito: Bronze)

```bash
pip install pandas pyarrow pytest
python -m pytest -v
```

Para ingerir um arquivo real baixado do dashboard:
```bash
PYTHONPATH=src python -m dtcc.bronze --raw CFTC_CUMULATIVE_RATES_AAAA_MM_DD.csv --out data/bronze/dtcc
```

**Validado contra dado real:** o arquivo de 02/10/2026 (26.840 linhas,
110 colunas, ~16 MB) foi ingerido em 2,1s, resultando em ~1,9 MB de
Parquet (compressão ~8x). `tests/fixtures/dtcc_cumulative_sample.csv`
é um recorte real de 300 linhas (não sintético) usado nos testes.

### Fase 1 — AWS (Bronze)

Infra só para a Bronze por enquanto — Silver/Gold entram quando a lógica
de cada uma estiver pronta, mesmo princípio do `b3-pipeline-aws`.

```bash
cd terraform
terraform init
terraform apply -var prefix=SEUNOME-dtcclab -var budget_email=seu@email
BUCKET=$(terraform output -raw bucket)
aws s3 cp ../CFTC_CUMULATIVE_RATES_AAAA_MM_DD.csv s3://$BUCKET/raw/dtcc/
aws glue start-job-run --job-name $(terraform output -raw glue_job_bronze)
```

Depois, cria/atualiza as tabelas do Athena (`dtcc_bronze`,
`controle_execucoes`) com o script abaixo em vez de colar SQL no console
— ele busca `BUCKET`/`DATABASE`/`WORKGROUP` direto do Terraform e roda
o `CREATE TABLE`/`MSCK REPAIR` via API do Athena. Seguro rodar de novo a
qualquer momento (depois de cada ingestão nova, por exemplo):

```bash
./scripts/configurar_athena.sh
```

**Atenção ao orçamento:** este Terraform cria um **Budget novo**, separado
do `b3-pipeline-aws` (nomes diferentes, mesma conta). O gasto da conta é
cumulativo entre os dois laboratórios — os US$ 10/mês daqui somam com os
do outro projeto, não são limites independentes do total de créditos.

**Ao terminar: `terraform destroy`.**

### Ingestão do arquivo de origem

**Automática desde 04/10/2026** — uma Lambda (`lambda/ingerir_cumulative.py`)
roda a mesma cópia **servidor-a-servidor** (API `CopyObject`, o arquivo
nunca passa pelo seu computador) sozinha, numa agenda fixa do
EventBridge (dias úteis, horário em `config/fontes/dtcc.yaml` ->
`ingestao_cron`). Não é mais um passo manual — depois do `terraform
apply`, o pipeline inteiro roda sem intervenção, do download ao alerta
de atualidade.

`scripts/ingerir_cumulative.sh` continua existindo para dois casos que a
Lambda agendada não cobre: um backfill manual (reprocessar uma data
específica ou outra classe de ativo) e rodar antes de a Lambda existir
numa conta nova:

```bash
cd ~/dtcc-pipeline-aws
./scripts/ingerir_cumulative.sh                      # hoje, RATES, cftc
./scripts/ingerir_cumulative.sh 2026-10-02            # data específica
./scripts/ingerir_cumulative.sh 2026-10-02 CREDITS    # outra classe de ativo
```

A partir da chegada do `.zip` (pela Lambda agendada ou pelo script
manual) o restante do pipeline é automático: uma Lambda descompacta,
uma state machine do Step Functions orquestra Glue + checagem de
qualidade em paralelo, e uma checagem agendada cobre atualidade/linhagem.
O "porquê" de cada peça está em [`docs/DECISOES.md`](docs/DECISOES.md).

## Arquitetura na AWS, hoje

```
EventBridge (agenda, dias úteis) -> Lambda ingerir_cumulative --(CopyObject)--> raw/dtcc_zip/*.zip
  (ou scripts/ingerir_cumulative.sh, manual -- backfill/data específica)
  -> Step Functions (aws_sfn_state_machine.pipeline)
       1. unzip_dtcc (Lambda)            -> raw/dtcc/*.csv
       2. em paralelo (Parallel):
          - Glue Bronze (glue:startJobRun.sync)
            - Job Bookmarks (só processa arquivo novo)
            - Glue Data Quality (schema, domínio, unicidade)
            -> bronze/dtcc/ (Parquet)
          - quality_check (Lambda)
            - volume absoluto + volume vs. histórico (Athena)
  -> job_concluido (Lambda, evento nativo "Glue Job State Change")
  -> checar_pipeline (Lambda, agendada em dias úteis)
       - atualidade (rodou ontem?) + linhagem (todas as etapas apareceram?)
       - execuções travadas (Step Functions, statusFilter=RUNNING)
  -> alertas: SNS (e-mail) | observabilidade: tabela controle_execucoes (Athena)
```

Cada peça desse diagrama — por que essa escolha de serviço AWS e não
outra, quais bugs reais apareceram, quais limites foram aceitos de
propósito — está documentada em
[`docs/DECISOES.md`](docs/DECISOES.md), na ordem em que foi construída.
Uma versão visual e interativa deste fluxo (com simulação passo a passo)
está publicada como artifact e pode ser pedida a quem tem acesso à
sessão que a gerou.

## Ferramentas de desenvolvimento

Este repositório é trabalhado com o [Claude Code](https://claude.com/claude-code).
Pra quem for continuar o desenvolvimento (Terraform, Lambdas Python, Glue
jobs) com Claude Code, o plugin oficial da AWS **`aws-core`** (publicado
pela própria Amazon Web Services, tier `partner` no catálogo) traz skills
especialistas nos serviços usados aqui — IAM, Lambda, Step Functions,
CloudFormation/CDK, storage, segurança, observabilidade, well-architected
review, entre outros — além de um servidor MCP (`aws-mcp`) para consultar
a documentação da AWS direto na conversa.

O plugin em si não é um arquivo versionado — ele é instalado por
máquina/conta, não por `git clone`. Mas a *recomendação do projeto* está
versionada em `.claude/settings.json` (`enabledPlugins:
"aws-core@claude-plugins-official"`), que é o mecanismo nativo do Claude
Code pra declarar "este projeto usa este plugin". Isso só habilita a
intenção no projeto; o Claude Code CLI/desktop ainda pede, uma vez por
máquina, `claude plugin install aws-core@claude-plugins-official` pra
baixar o plugin de fato. Sessões cloud (claude.ai/code) não carregam
plugins de projeto — esta configuração vale só pro CLI/desktop local.

Repositório upstream do plugin: https://github.com/aws/agent-toolkit-for-aws
(caminho `plugins/aws-core`).

## Tarefas futuras (ainda não construídas)

- **Consistência sazonal**: a dimensão de qualidade que nem o Glue Data
  Quality nem a checagem de atualidade/linhagem cobrem — volume
  esperado variando por dia da semana/época do ano, em vez de uma média
  simples dos últimos N dias (`_media_historica`, em
  `lambda/quality_check.py`).
- **Generalizar pra outras fontes (passos 2 e 3)**: transformar os
  recursos do Terraform num módulo reutilizável, instanciado uma vez
  por fonte a partir do seu `config/fontes/<nome>.yaml` — hoje só o
  passo 1 (o contrato de configuração) está feito (ver
  `docs/DECISOES.md`).

## Roteiro de evolução

1. **Bronze local** (feito) — ingestão crua do CSV Cumulative, sem
   converter tipo nenhum, particionado por arquivo de origem.
2. **Bronze na AWS** (feito) — Terraform + Glue para a mesma ingestão,
   rodando na nuvem em vez de local.
3. **Silver** — resolver a cadeia de eventos: para cada swap (agrupado
   pelo id original), aplicar os eventos em ordem de `Event timestamp` e
   produzir o estado atual (last-write-wins, descartando o histórico
   intermediário) + uma tabela de histórico completo (para auditoria).
   Local e AWS, como na Bronze.
4. **Gold** — métricas de negócio sobre o estado atual: volume por
   produto, distribuição de prazos, concentração por contraparte
   (campos exatos a definir quando a Silver estiver pronta).
5. **Intraday** — trocar o relatório Cumulative (diário) pelo Slice
   (publicado a cada ~15min) com polling agendado (Lambda + EventBridge),
   completando a progressão até dado de verdade quase em tempo real.

## Avisos honestos

- O schema (110 colunas) foi extraído de um arquivo real baixado em
  02/10/2026 — pode mudar se o DTCC alterar o layout; confira o header
  do CSV se usar outra data.
- A infraestrutura AWS cobre só a Bronze por enquanto (1 job Glue). Silver
  e Gold ganham seus próprios jobs Terraform quando a lógica de cada
  camada estiver pronta e validada localmente, não antes.
- O Terraform ainda não foi aplicado numa conta AWS real neste projeto
  (diferente do `b3-pipeline-aws`, que já rodou ponta a ponta) — revise o
  `terraform plan` com atenção antes do primeiro `apply`.
- Este projeto é de laboratório pessoal, com dado público do DTCC — sem
  nenhuma conexão com sistemas ou infraestrutura do Itaú.
