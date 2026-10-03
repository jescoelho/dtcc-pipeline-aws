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

Depois, cria/atualiza as tabelas do Athena (`dtcc_bronze`,
`controle_execucoes`) com o script abaixo em vez de colar SQL no console
-- ele busca `BUCKET`/`DATABASE`/`WORKGROUP` direto do Terraform e roda
o `CREATE TABLE`/`MSCK REPAIR` via API do Athena:

```bash
./scripts/configurar_athena.sh
```

Seguro rodar de novo a qualquer momento (depois de cada ingestão nova,
por exemplo, pra enxergar a partição do dia) -- `CREATE TABLE` usa
`IF NOT EXISTS` e `MSCK REPAIR` só adiciona partições novas. As queries
de exemplo (contagem de linhas, distribuição de `Action type`, volume e
qualidade por dia) continuam em `athena/queries.sql`, pra rodar
manualmente quando quiser explorar os dados -- essas não são
automatizadas de propósito, são consulta, não setup.

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

### Boas práticas, etapa 2: retenção de log das Lambdas

Outro achado da mesma avaliação: o log group de cada Lambda é criado
automaticamente no primeiro invoke com retenção **"nunca expira"** --
custo de armazenamento de CloudWatch Logs crescendo pra sempre, sem
necessidade num laboratório que não precisa de histórico de meses.
`terraform/log_retention.tf` define `retention_in_days = 14` pras três
(`unzip_dtcc`, `trigger_bronze`, `quality_check`).

**Passo manual necessário antes do `apply`**: os log groups já existem
(foram criados pelas invocações que já rodamos), então o Terraform
precisa importá-los pro estado em vez de criar do zero -- senão o
`apply` falha com "log group already exists":

```bash
cd terraform
terraform import aws_cloudwatch_log_group.unzip_dtcc /aws/lambda/jessica-dtcclab-unzip-dtcc
terraform import aws_cloudwatch_log_group.trigger_bronze /aws/lambda/jessica-dtcclab-trigger-bronze
terraform import aws_cloudwatch_log_group.quality_check /aws/lambda/jessica-dtcclab-quality-check
```

Depois do import, o `plan` deve mostrar só `retention_in_days` mudando
de `null` pra `14` em cada um -- `~ update in-place`, nada de
criar/destruir.

**Pegadinha real nessa etapa**: `terraform import` carrega a configuração
inteira pra saber o schema do recurso, então precisa das mesmas
`-var` que o `plan`/`apply` -- sem elas, fica esperando digitar o valor
na hora. E como o argumento do import (`/aws/lambda/...`) começa com
barra, o Git Bash no Windows tenta convertê-lo pra um caminho do
Windows (mesmo mangling do `aws logs tail`, ver etapa de Job Bookmarks)
-- resolve com `export MSYS_NO_PATHCONV=1` antes do import, `unset`
depois.

### Boas práticas, etapa 3: tabela de controle das execuções

Pergunta que motivou esta etapa: entre reter log do CloudWatch e ter uma
tabela de logs consultável, qual é preferível em engenharia de dados?
Resposta curta: não são concorrentes, mas se só um existisse, a tabela
ganha -- CloudWatch é bom pra depurar um erro específico (texto solto,
não consultável com SQL), a tabela é o que vira tendência, dashboard e
auditoria.

`lambda/trigger_bronze.py` e `lambda/quality_check.py` agora gravam um
registro JSON em `logs/execucoes/dt=AAAA-MM-DD/<uuid>.json` a cada
execução -- `origem` (qual Lambda), `csv`, `status`, e os campos
específicos de cada uma (`job_run_id` pra trigger_bronze; `linhas` e
`problemas` pra quality_check). `athena/queries.sql` tem a
`CREATE EXTERNAL TABLE controle_execucoes` (JSON SerDe, particionada por
`dt`) e três queries de exemplo: volume processado por dia, taxa de
problemas de qualidade por dia, e todo disparo do Glue com seu
`job_run_id` (pra cruzar com `aws glue get-job-run` se precisar
investigar).

Isso é a base que faltava pra fechar a extensão futura da checagem de
qualidade ("comparar o volume do dia com o histórico, não só o arquivo
isolado") -- agora o histórico existe e é consultável, só falta
escrever a query/alerta que compara.

Testado com S3 mockado (`tests/test_lambda_trigger_bronze.py`,
`tests/test_lambda_quality_check.py`): confirma que o registro é
gravado com `Bucket`/`Key`/conteúdo corretos, inclusive o `status`
batendo com "ok" vs "problema".

**Limite assumido nesta etapa**: assim como a tabela `dtcc_bronze`, o
`MSCK REPAIR TABLE controle_execucoes` precisa rodar de novo pra
enxergar partições (`dt=`) novas -- manual por enquanto, mesma lacuna
de sempre.

**Fora do escopo desta etapa**: os log groups do Glue (`/aws-glue/jobs/...`)
são compartilhados por toda a conta, não um por job -- ajustar a
retenção deles afetaria qualquer outro laboratório que usar Glue nesta
mesma conta, então não entram aqui.

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

### Bug real encontrado: filtro do EventBridge deixava passar CSV errado

Ao validar a tabela de controle (`controle_execucoes`) com dados reais,
apareceram dois registros de `trigger_bronze` disparados para
`athena-results/<uuid>.csv` -- os arquivos de resultado que o próprio
Athena grava no bucket a cada query, não um CSV do DTCC.

Causa: o `event_pattern` da regra `csv_arrived`
(`terraform/lambda.tf`) combinava prefixo e sufixo no mesmo array de
`key`:

```hcl
key = [
  { prefix = "raw/dtcc/" },
  { suffix = ".csv" },
]
```

A documentação da AWS descreve essa combinação como um **AND**
especial (prefixo E sufixo), mas na prática, nesse nível de
aninhamento (`detail.object.key`), ela se comportou como **OR** --
qualquer `.csv` do bucket bateu com a regra, não só os de
`raw/dtcc/`. Resultado real: 2 execuções do Glue Bronze disparadas à
toa pelos CSVs de resultado do Athena.

**Correção**: trocar a combinação prefixo+sufixo por só o prefixo.
`raw/dtcc/` é exclusivo da Lambda `unzip_dtcc` -- nada mais escreve
ali -- então o prefixo sozinho já identifica o evento certo, sem
depender de uma combinação que não se mostrou confiável:

```hcl
key = [
  { prefix = "raw/dtcc/" },
]
```

**Lição**: documentação de comportamento "especial" de serviço
gerenciado (como essa combinação AND) merece validação empírica antes
de confiar nela em produção -- o jeito mais simples de descobrir o
problema foi justamente construir a tabela de controle estruturada
(etapa 3) e **olhar os dados reais**, não inspecionar o Terraform.

### Boas práticas, etapa 4: fluxo end-to-end completo na tabela de controle

Pergunta que motivou esta etapa: a tabela de controle da etapa 3 estava
completa, ou faltava alguma coisa pra cobrir o fluxo end-to-end? Três
lacunas reais:

1. **A descompactação não deixava rastro.** `lambda/unzip_dtcc.py` agora
   grava um registro (`origem="unzip_dtcc"`, `zip` de origem, `csv`
   gerado, `status="descompactado"`) -- antes, a chegada do `.zip` só
   existia no CloudWatch.
2. **Faltava o desfecho do Glue.** `trigger_bronze` só gravava
   `status="disparado"` (o início) -- não existia, na tabela, se aquele
   `job_run_id` terminou com sucesso, falhou, ou quanto tempo levou.
   Nova Lambda `lambda/job_concluido.py`, disparada por um evento nativo
   do Glue (`Glue Job State Change`, via `aws_cloudwatch_event_rule.glue_bronze_concluido`
   em `terraform/observabilidade.tf`) pra **todo** desfecho (sucesso
   incluso -- diferente da regra de alerta por e-mail da etapa 1, que é
   só pra falha, de propósito). Grava `origem="glue_job"`, `status`
   (`succeeded`/`failed`/`timeout`/...) e `duracao_segundos`
   (`ExecutionTime`, vindo de `glue:GetJobRun`).
3. **Não dava pra ligar as etapas de forma confiável.** Juntar
   "disparado" com "checagem de qualidade" só por nome de arquivo +
   proximidade de horário é frágil (ambíguo se o mesmo CSV for
   reprocessado). Agora existe um `execution_id` (uuid) que nasce no
   `unzip_dtcc.py`, viaja como metadado do objeto S3
   (`Metadata={"execution-id": ...}`), é lido de volta por
   `trigger_bronze.py` e `quality_check.py` via `head_object`, e é
   propagado pro Glue como argumento de job (`--execution_id`), de onde
   `job_concluido.py` o recupera no fim via `glue:GetJobRun`. As quatro
   etapas ficam ligadas pelo mesmo `execution_id` -- ver a query "Fluxo
   completo de uma execução" em `athena/queries.sql`, que junta tudo numa
   linha por execução.

`scripts/configurar_athena.sh` agora recria a tabela `controle_execucoes`
(`DROP TABLE` + `CREATE`, só metadado -- os dados em
`logs/execucoes/` não são tocados) em vez de só `CREATE IF NOT EXISTS`,
porque o schema ganhou colunas novas (`zip`, `execution_id`,
`duracao_segundos`) e `CREATE EXTERNAL TABLE IF NOT EXISTS` não atualiza
o schema de uma tabela que já existe.

Testado com Glue/S3 mockados
(`tests/test_lambda_unzip.py`, `tests/test_lambda_trigger_bronze.py`,
`tests/test_lambda_quality_check.py`, novo
`tests/test_lambda_job_concluido.py`): confirma o metadado no objeto, a
propagação do argumento `--execution_id` pro `start_job_run`, o
fallback (gera um novo `execution_id` em vez de quebrar quando o
metadado não existe -- ex.: CSV colocado manualmente) e o `status` em
minúsculas gravado a partir do `state` do evento do Glue.

**Limite assumido nesta etapa**: se `head_object` falhar (objeto
apagado entre o evento e a leitura, por exemplo), a Lambda quebra sem
fallback -- diferente do caso "metadado ausente", que já é tratado.
Não aconteceu até agora; fica registrado como risco conhecido, não
como bug.

### Bug real encontrado: quality_check ficava sem memória no arquivo real

Ao testar o fluxo end-to-end contra uma ingestão de verdade, o
`quality_check` esgotou os 128 MB de memória da Lambda nas 3 tentativas
(`Runtime.OutOfMemory`, visível no `aws logs tail`) e a execução foi pra
DLQ. Causa: o código carrega o CSV inteiro na memória
(`obj["Body"].read().decode(...)`) antes de processar, e o relatório
"Cumulative" do DTCC cresce com o tempo (acumula posições abertas) --
128 MB, calibrado contra arquivos de teste pequenos, não é suficiente
pro arquivo real atual.

Correção aplicada: `terraform/quality_check.tf` subiu a memória da
Lambda pra 1024 MB e o timeout de 30 pra 60s (CPU no Lambda escala com a
memória, então o processamento também fica mais rápido, não só com mais
espaço).

**Limite que continua existindo**: a correção é calibrar um número
maior, não eliminar o problema de raiz -- é `O(tamanho do arquivo)` de
memória. Se o Cumulative continuar crescendo, isso estoura de novo em
algum momento. Correção definitiva seria processar o CSV em streaming
(iterar linha a linha a partir do `Body`, sem nunca ter o arquivo
inteiro na memória), não construída agora.

### Observabilidade, etapa 4: checagem de volume contra o histórico

Extensão que ficou pendente desde a criação da tabela de controle
(etapa 3 de boas práticas): antes, a checagem de qualidade só sabia se
o arquivo de hoje estava vazio (checagem absoluta) -- não dava pra
detectar uma queda real de volume (ex.: metade dos registros de sempre)
que ainda assim não é zero.

`lambda/quality_check.py` agora tem uma quarta checagem: lê os
registros de `quality_check` dos últimos `DIAS_HISTORICO` dias (7, por
enquanto) direto de `logs/execucoes/` -- sem passar pelo Athena, só
`list_objects_v2` + `get_object` no próprio S3, porque é pouco volume
de arquivos JSON por dia e isso evita a latência e a dependência de
partição atualizada (MSCK REPAIR) que uma query no Athena teria. Se o
volume de hoje cair mais de `QUEDA_MAXIMA_TOLERADA` (50%) da média
histórica, isso entra como mais um problema -- mesmo alerta por SNS que
as outras três checagens já usam.

Se não existir histórico ainda (primeiros dias do pipeline), a
checagem é simplesmente pulada -- não gera falso alerta comparando
contra nada.

Testado com S3 mockado simulando histórico
(`tests/test_lambda_quality_check.py`): confirma o alerta disparando
numa queda real, o silêncio numa variação normal, o comportamento sem
histórico, e que registros de outras origens (`trigger_bronze`,
`glue_job`) na mesma tabela não contaminam a média.

**Limites assumidos nesta etapa**:
- A média inclui dias com problema (não filtra por `status == "ok"`) --
  uma queda real e sustentada rebaixa a própria média que a detectaria,
  então uma degradação **gradual** pode passar sem alertar; só uma
  queda **abrupta** contra a média recente é pega. Comparar contra uma
  baseline mais robusta (mediana, ou só dias "ok") é extensão futura.
- Os limites (`DIAS_HISTORICO=7`, `QUEDA_MAXIMA_TOLERADA=0.5`) são
  números redondos de partida, não calibrados contra o comportamento
  real do Cumulative ao longo de semanas -- ajustar depois de observar
  alguns alertas (falsos positivos ou negativos) é esperado.

### AWS Glue Data Quality, validado e adotado para schema/domínio/unicidade

Pergunta que motivou isso: como automatizar qualidade/observabilidade
de forma que generalize pra qualquer tabela, não só pra este pipeline?
Resposta: várias dimensões de qualidade (completude, validade de
domínio, unicidade) já são um serviço nativo da AWS -- **Glue Data
Quality** (DQDL) -- em vez de código Python escrito à mão. Dado que o
pipeline já roda em Glue, valeu comparar antes de generalizar o motor
próprio.

`glue/bronze_ingest.py` roda um `EvaluateDataQuality` com um ruleset
DQDL cobrindo schema (`ColumnExists`, `IsComplete`), domínio
(`ColumnValues` pro Action type), volume absoluto (`RowCount > 0`) e
unicidade (`Uniqueness "Dissemination Identifier" > 0.99` -- checagem
que o Python **nunca teve**). O resultado é gravado em
`logs/execucoes/` com `origem="glue_data_quality"`, mesmo
`execution_id` do resto do fluxo.

**Bugs reais encontrados e corrigidos na primeira execução** (confirma
por que não dava pra testar isso localmente -- não existe Spark/Glue
Data Quality fora da AWS):
- `EvaluateDataQuality().process_rows(...)` devolve uma
  `DynamicFrameCollection`, não um `DynamicFrame` direto
  (`AttributeError: 'DynamicFrameCollection' object has no attribute
  'toDF'`). Corrigido extraindo a chave `"ruleOutcomes"` via
  `SelectFromCollection.apply(dfc=..., key="ruleOutcomes", ...)` --
  confirmado contra a documentação oficial da AWS antes de reaplicar,
  pra não ficar tentando adivinhar de novo.
- Validado com dado real depois da correção: as 8 regras rodaram,
  `RowCount` (27133) bateu exatamente com o `linhas` que o
  `quality_check.py` contou pro mesmo `execution_id`, e os dois
  vereditos concordaram (`ok`).

**Decisão tomada com base no resultado real**: aposentar as checagens
de schema e domínio do `quality_check.py` -- o Glue Data Quality cobre
essas duas dimensões de forma superior (métricas quantificadas como
`Completeness`/`Uniqueness`, não só um booleano) e sem duplicar a
mesma regra de negócio em dois lugares (Python aqui, DQDL lá).
`lambda/quality_check.py` ficou só com o que o Glue Data Quality **não
faz**: comparação de volume contra o histórico (o Glue DQ só vê o
arquivo de hoje, isolado) e o alerta via SNS em si (o Glue DQ só grava
métrica, não notifica). Os três testes de schema/domínio em
`tests/test_lambda_quality_check.py` foram removidos -- essa cobertura
agora é responsabilidade do ruleset DQDL, não testável aqui.

Decisão de escopo que persiste: `enableDataQualityResultsPublishing`
(repositório nativo de resultados do Glue) continua **desligado** --
`enableDataQualityCloudWatchMetrics` fica ligado (publica pass/fail
por regra como métrica, só precisa de `cloudwatch:PutMetricData`) e o
resumo próprio em `logs/execucoes/` já é consultável no Athena, que é
o que usamos pra comparar com o `quality_check.py`.

**Formato de armazenamento (03/10/2026): tidy, uma linha por regra.**
A primeira versão gravava um único registro por execução com todas as
8 regras dentro de uma coluna `regras` (string JSON) -- qualquer
consulta exigia `json_extract`. Reescrito pra gravar um registro por
regra avaliada (`regra`, `outcome`, `motivo_falha`, `metrica_nome`,
`metrica_valor`, além de `execution_id`/`status`/`timestamp` já
usados pelas outras etapas) -- filtra e agrupa direto em SQL
(`WHERE outcome = 'Failed'`, `GROUP BY regra`), sem parsear nada. Ver
`_registrar_data_quality` em `glue/bronze_ingest.py` e as queries de
detalhe em `athena/queries.sql`. Limite aceito: cada linha carrega no
máximo uma métrica (`metrica_nome`/`metrica_valor`); neste ruleset
cada regra nunca tem mais que uma, mas uma regra futura com múltiplas
métricas perderia as demais.

**Limite que persiste**: isto cobre só a Bronze, e só as dimensões
1-3 (completude, validade, unicidade) da taxonomia de qualidade que
discutimos. Atualidade, consistência sazonal e linhagem de dado
continuam fora do escopo do Glue Data Quality -- são sobre o
*pipeline*, não sobre o *dado isolado*, e continuam sendo papel da
tabela de controle (`logs/execucoes/`).

### Contrato de fonte -- passo 1 da generalização (03/10/2026)

`config/fontes/dtcc.yaml` é agora a única fonte de verdade pro que é
específico da fonte DTCC: caminhos S3 (`raw_prefix`, `zip_prefix`,
`bronze_prefix`), schema/identidade/domínio pro ruleset DQDL
(`colunas_obrigatorias`, `coluna_id`, `unicidade_minima`,
`coluna_dominio`, `valores_dominio`) e os parâmetros do volume
histórico (`dias_historico`, `queda_maxima_tolerada`). Antes, cada um
desses valores estava cravado em dois ou três lugares ao mesmo tempo
(Python e Terraform) -- mudar um significava achar todas as cópias.

**Como funciona**: só o Terraform lê o YAML (`yamldecode(file(...))`
em `terraform/main.tf`, como `local.fonte`) e distribui cada campo pro
recurso que já o consumia -- argumentos do Glue job, variáveis de
ambiente das Lambdas (`RAW_PREFIX`, `DIAS_HISTORICO`,
`QUEDA_MAXIMA_TOLERADA`), filtro do S3 notification, prefixo da regra
do EventBridge, recursos do IAM. O Python não ganhou um parser de YAML
-- continua lendo env var/job argument, exatamente como já lia
`raw_path`/`bronze_path`/`execution_id` antes. `glue/bronze_ingest.py`
monta o ruleset DQDL em runtime a partir dos argumentos
(`_montar_ruleset`), em vez de ter a string cravada.

**Limite que persiste (de propósito)**: isto generaliza o *dado de
configuração*, não ainda a *infraestrutura*. Os recursos do Terraform
continuam declarados um a um pra uma única fonte -- não é um módulo
reutilizável. Adicionar uma segunda fonte hoje significa duplicar os
recursos (Lambdas, IAM, Glue job, regras do EventBridge) com um
`config/fontes/<nome>.yaml` novo, não só adicionar um arquivo de
config. Virar módulo só vale a pena com uma segunda fonte real na mão,
pra não generalizar em cima de uma suposição. `scripts/ingerir_cumulative.sh`
(download inicial) e a leitura do CSV pelo Glue (header com as 110
colunas reais do Cumulative) também ficaram fora -- são específicos
demais do DTCC pra abstrair sem um segundo caso real.

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
- **Generalizar pra outras fontes (passos 2 e 3)**: transformar os
  recursos do Terraform num módulo reutilizável, instanciado uma vez
  por fonte a partir do seu `config/fontes/<nome>.yaml` -- hoje só o
  passo 1 (o contrato de configuração) está feito, ver seção acima.
- **Atualidade, consistência sazonal, linhagem de dado**: as três
  dimensões de qualidade que o Glue Data Quality não cobre (ver seção
  acima) -- pedem um runner agendado, não reativo a evento.

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
