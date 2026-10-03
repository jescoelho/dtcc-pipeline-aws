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

## O que o arquivo real revelou

Cada linha do Cumulative é um **evento de disseminação**, não uma
transação definitiva. A coluna `Action type` diz o que aconteceu: `NEWT`
(nova transação), `MODI` (modificação), `CORR` (correção), `TERM`
(encerramento), `EROR`, `REVI`. Uma linha de modificação referencia a
transação original via `Original Dissemination Identifier`:

```
5598983937000000601                          NEWT  19:00:00
5598983979000000301  -> 5598983937...000601   MODI  19:00:04
5598984059000000201  -> 5598983937...000601   MODI  19:00:07
```

Para saber o estado **atual** de uma transação é preciso reconstruir a
cadeia na ordem do tempo. Ver `docs/ESTUDO.md` para a explicação completa,
em linguagem simples.

## Fase 0 — local, sem AWS (feito: Bronze)

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

## Fase 1 — AWS (Bronze)

Infra só para a Bronze por enquanto -- Silver/Gold entram quando a lógica
de cada uma estiver pronta, mesmo princípio do `b3-pipeline-aws`.

```bash
cd terraform
terraform init
terraform apply -var prefix=SEUNOME-dtcclab -var budget_email=seu@email
BUCKET=$(terraform output -raw bucket)
aws s3 cp ../CFTC_CUMULATIVE_RATES_AAAA_MM_DD.csv s3://$BUCKET/raw/dtcc/
aws glue start-job-run --job-name $(terraform output -raw glue_job_bronze)
```

Depois, no Athena (workgroup criado pelo Terraform), rode `athena/queries.sql`
(troque `<BUCKET>` e `<DATABASE>`) para confirmar que a contagem de linhas
e a distribuição de `Action type` bateram com o CSV original.

**Atenção ao orçamento:** este Terraform cria um **Budget novo**, separado
do `b3-pipeline-aws` (nomes diferentes, mesma conta). O gasto da conta é
cumulativo entre os dois laboratórios -- os US$ 10/mês daqui somam com os
do outro projeto, não são limites independentes do total de créditos.

**Ao terminar: `terraform destroy`.**

### Automatizando a ingestão (`scripts/ingerir_cumulative.sh`)

**Escopo atual: só a cópia pro S3, nada além disso.** O script copia o
`.zip` do Cumulative direto do bucket público do DTCC pro nosso bucket
(`raw/dtcc_zip/`), usando uma cópia **servidor-a-servidor** (`aws s3 cp`
entre dois `s3://`, via API `CopyObject`) -- o arquivo nunca passa pelo
seu computador, nem em disco nem em memória. Confirmado manualmente em
03/10/2026.

```bash
cd ~/dtcc-pipeline-aws
./scripts/ingerir_cumulative.sh                      # hoje, RATES, cftc
./scripts/ingerir_cumulative.sh 2026-10-02            # data específica
./scripts/ingerir_cumulative.sh 2026-10-02 CREDITS    # outra classe de ativo
```

### Descompactando sem passar pela sua máquina (Lambda)

Quando o `.zip` cai em `raw/dtcc_zip/`, uma Lambda (`lambda/unzip_dtcc.py`)
é disparada automaticamente pelo próprio evento do S3 -- lê o zip pra
memória, acha o `.csv` dentro, e grava ele em `raw/dtcc/`. Nada disso passa
pelo seu computador nem por um Glue job; é só a Lambda, acordada pelo S3.

Testado localmente (`tests/test_lambda_unzip.py`, com mocks de S3 e um zip
real construído a partir do fixture) antes de ir para o Terraform. Para
aplicar:
```bash
cd terraform
terraform apply -var prefix=SEUNOME-dtcclab -var budget_email=seu@email
./../scripts/ingerir_cumulative.sh 2026-10-02    # dispara o fluxo
aws s3 ls s3://$(terraform output -raw bucket)/raw/dtcc/   # confere se o csv apareceu
```

### Disparando o Glue Bronze automaticamente (Lambda separada, Opção 2)

Decisão: usar uma Lambda **dedicada só a isso** (`lambda/trigger_bronze.py`),
disparada quando o CSV aparece em `raw/dtcc/`, em vez de a mesma Lambda
que descompacta também chamar o Glue. Motivo: responsabilidade única --
a Lambda de descompactar já está testada isoladamente sem precisar
mockar Glue; se o `start_job_run` falhar, isso não deve derrubar a
descompactação, que já funcionou; e dá pra encaixar outras reações ao
mesmo evento (checagem de qualidade -- ver abaixo) sem tocar na Lambda de
descompactar.

**Como o evento chega até ela:** o `.csv` em `raw/dtcc/` tem **dois**
consumidores independentes (`trigger_bronze` e a checagem de qualidade,
abaixo). O S3 não aceita duas regras com o mesmo prefixo+sufixo apontando
para Lambdas diferentes (erro real que apareceu ao aplicar: "Configuration
is ambiguously defined") -- ele não sabe que as duas devem disparar. Por
isso o bucket manda esse evento também pro **EventBridge**
(`eventbridge = true` no `aws_s3_bucket_notification`), e uma única regra
do EventBridge (`aws_cloudwatch_event_rule.csv_arrived`) tem os dois como
alvos. O `.zip` continua indo direto do S3 pra Lambda de descompactar --
um só consumidor, sem ambiguidade, sem precisar do EventBridge.

**Pegadinha real que isso trouxe:** o formato do evento que chega na
Lambda muda conforme o caminho. Direto do S3 (`unzip_dtcc`), é
`{"Records": [{"s3": {"bucket": ..., "object": ...}}]}`. Via EventBridge
(`trigger_bronze`, `quality_check`), é o evento nativo "S3 Object
Created": `{"detail": {"bucket": {"name": ...}, "object": {"key":
...}}}`, sem `Records` nenhum. Rodar `aws logs tail` nas duas Lambdas
depois do primeiro `apply` mostrou exatamente esse erro (`KeyError:
'Records'`) -- as duas liam o evento como se fosse o formato antigo.

Cadeia completa agora:
```
.zip em raw/dtcc_zip/ -> Lambda descompacta
.csv em raw/dtcc/ -> EventBridge rule -> [trigger_bronze, quality_check]  (paralelo)
```

**Aviso honesto sobre concorrência, parcialmente resolvido pelos Job
Bookmarks (abaixo):** se dois CSVs chegarem muito próximos um do outro,
dois disparos do job ainda podem rodar concorrentemente -- isso os Job
Bookmarks não evitam (são por execução, não um lock entre execuções
simultâneas). O que eles eliminam é o sintoma mais caro: hoje cada
execução processa só os arquivos novos, não a pasta inteira, então a
sobreposição fica barata e rápida em vez de redundante e cara. Resolver
a concorrência de verdade (execução única por vez) é o que a etapa de
Step Functions (roteiro, item 2) cobre.

### Boas práticas, etapa 1: Job Bookmarks no Glue Bronze

Achado da mesma avaliação que trouxe a DLQ: sem isso, cada execução do
job relia `raw/dtcc/` **inteira**, inclusive arquivos já processados em
runs anteriores -- custo e tempo crescendo sem necessidade a cada dia
novo, e nenhum controle sobre reingestão duplicada do mesmo arquivo.

`--job-bookmark-option = "job-bookmark-enable"` (Terraform) faz o Glue
rastrear quais arquivos já leu com sucesso e processar só os novos a
cada execução. Exige ler via `glueContext.create_dynamic_frame.from_options`
em vez de `spark.read.csv` direto -- o bookmark é um recurso do Glue, não
do Spark puro (ver comentários em `glue/bronze_ingest.py`).

**Pegadinha operacional a saber:** na primeira execução depois de
habilitar, o Glue não tem bookmark anterior, então processa tudo que já
existe em `raw/dtcc/` uma última vez (comportamento esperado, não um
bug). Se um dia você precisar forçar reprocessar tudo de novo
propositalmente (um backfill, por exemplo), o bookmark tem que ser
resetado explicitamente -- `aws glue reset-job-bookmark --job-name
jessica-dtcclab-bronze-ingest` -- senão o job só vê os arquivos que
chegarem depois do reset.

**Limite assumido nesta etapa**: resolve custo/tempo e a maior parte da
duplicidade (reler o mesmo arquivo sem necessidade). Não resolve duas
execuções simultâneas colidindo (ver aviso acima) -- isso é Step
Functions.

### Observabilidade, etapa 1: avisar quando o Glue Bronze falhar

Caminho nativo da AWS, sem Lambda nova (`terraform/observabilidade.tf`):
o próprio Glue emite um evento de mudança de estado; uma regra do
**EventBridge** filtra esse evento para `FAILED`/`TIMEOUT`/`ERROR` e
publica num tópico **SNS**, que manda e-mail pro mesmo endereço do alerta
de custo (`var.budget_email`).

```
Glue Job State Change (evento nativo)
  -> EventBridge rule (filtra: job = bronze, state = FAILED/TIMEOUT/ERROR)
  -> SNS topic
  -> e-mail
```

De propósito, cobre só **falha** -- não "sucesso". Um e-mail por execução
bem-sucedida, todo dia, é ruído que ensina a ignorar o canal; falha é o
caso em que alguém realmente precisa olhar.

**Depois do `terraform apply`, a AWS manda um e-mail de confirmação da
inscrição no SNS** ("AWS Notification - Subscription Confirmation") --
sem clicar em "Confirm subscription" nesse e-mail, os alertas não chegam.

Isto cobre só "o job rodou e explodiu". Não cobre "o dado que chegou está
estranho mas o job roda sem erro" -- isso é a checagem de qualidade
abaixo.

### Observabilidade, etapa 2: checagem de qualidade do CSV

`lambda/quality_check.py`, disparada pela **mesma regra do EventBridge**
que dispara a `trigger_bronze` (csv em `raw/dtcc/`, ver explicação do
mecanismo na seção anterior), como alvo independente da mesma regra --
roda em paralelo, **não bloqueia o Glue**. Checagens, as três mais
simples que cobrem o essencial:

1. **Schema**: as colunas-chave usadas pela Bronze/Silver estão
   presentes (`Dissemination Identifier`, `Original Dissemination
   Identifier`, `Action type`, `Event timestamp`).
2. **Volume**: o arquivo não chegou vazio.
3. **Domínio**: todo valor de `Action type` está no conjunto conhecido
   (`NEWT`/`MODI`/`CORR`/`TERM`/`EROR`/`REVI`) -- um valor novo pode ser
   o DTCC mudando o layout, o que já aconteceu uma vez neste projeto.

Se algum problema aparecer, publica no **mesmo tópico SNS** da falha do
Glue -- um único canal de alerta para "pipeline com problema", não
importa a causa. Se o arquivo estiver ok, não publica nada (mesmo
princípio do alerta de falha: silêncio é o estado normal).

Testado com S3 e SNS mockados (`tests/test_lambda_quality_check.py`,
5/5): CSV válido, CSV vazio, coluna ausente, `Action type` desconhecido,
múltiplos arquivos no mesmo evento.

**Limite assumido nesta etapa**: a checagem só olha o CSV isoladamente
(schema/volume/domínio). Não compara com o volume do dia anterior nem
detecta uma queda gradual -- isso é uma extensão futura, não construída.

### Observabilidade, etapa 3: dead-letter queue nos alvos do EventBridge

Achado de uma avaliação de boas práticas do pipeline inteiro: sem isso,
uma entrega que falhar na regra `csv_arrived` (throttle da Lambda, erro
transiente da AWS, um bug novo) faz o evento **desaparecer
silenciosamente** -- sem log, sem alerta. É um ponto cego exatamente na
entrega do evento que alimenta a `trigger_bronze` e a `quality_check`, o
que esvazia o propósito da observabilidade das etapas 1 e 2 se a entrega
em si falhar sem deixar rastro.

`terraform/dlq.tf` cria uma fila SQS (`${prefix}-eventos-falhos`) como
destino de falha dos dois alvos (`dead_letter_config`), com
`retry_policy` de até 3 tentativas em até 1h antes de cair na fila. A
mensagem que cai na DLQ carrega o evento original -- dá pra reprocessar
manualmente depois de entender a causa.

**Limite assumido nesta etapa**: a DLQ guarda a mensagem, mas ninguém é
avisado quando algo cai nela -- monitorar a fila (alarme no
`ApproximateNumberOfMessagesVisible`, ligado ao mesmo tópico SNS) é
extensão natural, não construída agora.

## Tarefas futuras (ainda não construídas)

- **Agendar a ingestão**: mover `scripts/ingerir_cumulative.sh` (o
  download inicial) para dentro de uma Lambda com EventBridge Schedule,
  rodando sozinho todo dia, sem depender do computador estar ligado --
  único passo manual que resta em todo o pipeline.
- **Step Functions**: hoje, dois CSVs chegando perto um do outro disparam
  dois Glue runs concorrentes sobre a mesma pasta (inofensivo, mas
  redundante -- ver aviso na seção da `trigger_bronze`). Step Functions
  resolve isso com execução única por vez, e também orquestraria
  Bronze → Silver → Gold em sequência.
- **Qualidade de dados, extensão futura**: comparar o volume do dia com
  o histórico (detectar queda gradual), não só o arquivo isolado.

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
