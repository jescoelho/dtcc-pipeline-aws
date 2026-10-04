# Decisões de arquitetura e histórico de evolução

Este documento é o **changelog narrativo** do pipeline: cada decisão de
desenho, bug real encontrado e extensão de observabilidade, na ordem em
que aconteceram, com o raciocínio por trás e os limites que ficaram
assumidos conscientemente. O `README.md` cobre o "como rodar"; este
arquivo cobre o "por que ficou assim" -- leia-o quando precisar entender
uma decisão específica (Ctrl+F pelo nome do recurso ou da Lambda) ou
quer o histórico completo antes de alterar algo.

## Descompactando sem passar pela sua máquina (Lambda)

Quando o `.zip` cai em `raw/dtcc_zip/`, uma Lambda (`lambda/unzip_dtcc.py`)
é disparada automaticamente pelo próprio evento do S3 -- lê o zip pra
memória, acha o `.csv` dentro, e grava ele em `raw/dtcc/`. Nada disso passa
pelo seu computador nem por um Glue job; é só a Lambda, acordada pelo S3.

Testado localmente (`tests/test_lambda_unzip.py`, com mocks de S3 e um zip
real construído a partir do fixture) antes de ir para o Terraform.

## Disparando o Glue Bronze automaticamente (Lambda separada, Opção 2)

Decisão: usar uma Lambda **dedicada só a isso** (`lambda/trigger_bronze.py`,
hoje aposentada -- ver seção Step Functions), disparada quando o CSV
aparece em `raw/dtcc/`, em vez de a mesma Lambda que descompacta também
chamar o Glue. Motivo: responsabilidade única -- a Lambda de descompactar
já está testada isoladamente sem precisar mockar Glue; se o `start_job_run`
falhar, isso não deve derrubar a descompactação, que já funcionou; e dá
pra encaixar outras reações ao mesmo evento (checagem de qualidade --
abaixo) sem tocar na Lambda de descompactar.

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

Cadeia completa na época (antes de virar Step Functions -- ver abaixo):
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
Step Functions cobre.

## Boas práticas, etapa 1: Job Bookmarks no Glue Bronze

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

## Boas práticas, etapa 2: retenção de log das Lambdas

Outro achado da mesma avaliação: o log group de cada Lambda é criado
automaticamente no primeiro invoke com retenção **"nunca expira"** --
custo de armazenamento de CloudWatch Logs crescendo pra sempre, sem
necessidade num laboratório que não precisa de histórico de meses.
`terraform/log_retention.tf` define `retention_in_days = 14`.

**Passo manual necessário antes do `apply`, só para os log groups que já
existiam na época desta etapa** (`unzip_dtcc` e `quality_check` --
criados pelas invocações que já tínhamos rodado manualmente antes de o
Terraform gerenciar isso): o Terraform precisa importá-los pro estado em
vez de criar do zero, senão o `apply` falha com "log group already
exists":

```bash
cd terraform
terraform import aws_cloudwatch_log_group.unzip_dtcc /aws/lambda/jessica-dtcclab-unzip-dtcc
terraform import aws_cloudwatch_log_group.quality_check /aws/lambda/jessica-dtcclab-quality-check
```

(A referência a `trigger_bronze` que existia aqui ficou desatualizada
quando essa Lambda foi aposentada -- ver seção Step Functions; o recurso
nem existe mais em `log_retention.tf`. Os log groups criados **depois**
que o Terraform passou a existir -- `iniciar_pipeline`, `checar_pipeline`
-- não precisam de import: o próprio Terraform cria cada um já com a
Lambda correspondente, sem invocação manual prévia.)

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

## Boas práticas, etapa 3: tabela de controle das execuções

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

## Observabilidade, etapa 1: avisar quando o Glue Bronze falhar

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

## Observabilidade, etapa 2: checagem de qualidade do CSV

`lambda/quality_check.py`, disparada pela **mesma regra do EventBridge**
que dispara a `trigger_bronze` (csv em `raw/dtcc/`, ver explicação do
mecanismo acima), como alvo independente da mesma regra -- roda em
paralelo, **não bloqueia o Glue**. Checagens, as três mais simples que
cobrem o essencial:

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
detecta uma queda gradual -- ver etapa 4 abaixo, que fechou essa lacuna.

## Observabilidade, etapa 3: dead-letter queue nos alvos do EventBridge

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

## Bug real encontrado: filtro do EventBridge deixava passar CSV errado

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
(etapa 3 acima) e **olhar os dados reais**, não inspecionar o Terraform.

## Boas práticas, etapa 4: fluxo end-to-end completo na tabela de controle

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
`tests/test_lambda_quality_check.py`, `tests/test_lambda_job_concluido.py`):
confirma o metadado no objeto, a propagação do argumento `--execution_id`
pro `start_job_run`, o fallback (gera um novo `execution_id` em vez de
quebrar quando o metadado não existe -- ex.: CSV colocado manualmente) e
o `status` em minúsculas gravado a partir do `state` do evento do Glue.

**Limite assumido nesta etapa**: se `head_object` falhar (objeto
apagado entre o evento e a leitura, por exemplo), a Lambda quebra sem
fallback -- diferente do caso "metadado ausente", que já é tratado.
Não aconteceu até agora; fica registrado como risco conhecido, não
como bug.

## Bug real encontrado: quality_check ficava sem memória no arquivo real

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

## Observabilidade, etapa 4: checagem de volume contra o histórico

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

## AWS Glue Data Quality, validado e adotado para schema/domínio/unicidade

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

## Contrato de fonte -- passo 1 da generalização (03/10/2026)

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

## Step Functions -- orquestração do pipeline Bronze (03/10/2026)

**Motivação real**: em produção, o mesmo arquivo (`CFTC_CUMULATIVE_RATES_2026_10_02.csv`)
gerou duas execuções completas e independentes do pipeline -- não
concorrentes (`MaxConcurrentRuns` do Glue já é 1 por padrão, bloquearia
isso se fossem simultâneas), **sequenciais**: a notificação do S3
chegou duplicada (S3/EventBridge documentam entrega "at-least-once",
não "exactly-once"), e cada entrega disparava o fluxo inteiro de novo,
sem nenhuma noção de "isso já rodou". Sem corrupção de dado (a escrita
da Bronze já era idempotente por partição), mas processamento e custo
desperdiçados, e dois registros concorrendo na tabela de controle pra
um único arquivo real.

**O que mudou**: o encadeamento de Lambdas + regras do EventBridge
(`unzip_dtcc` disparado direto pelo S3 -> `trigger_bronze` e
`quality_check` como alvos independentes da mesma regra) virou uma
state machine (`aws_sfn_state_machine.pipeline`, ver
`terraform/step_functions.tf`):

1. **Descompactar** (Task, invoca `unzip_dtcc.py`, sem mudar seu código)
2. **Etapas em paralelo** (Parallel): o Glue (`glue:startJobRun.sync`
   -- a state machine agora *espera* o job terminar, em vez de só
   disparar e seguir) e a checagem de qualidade (`quality_check.py`,
   sem mudar seu código) rodam ao mesmo tempo, exatamente como antes.

`trigger_bronze.py` foi **aposentado** -- sua única responsabilidade
(chamar `glue:StartJobRun` e logar "disparado") virou um Task nativo da
state machine; o log de "disparado" deixa de existir a partir daqui
(histórico anterior continua consultável, ver nota em `athena/queries.sql`).
`job_concluido.py` **não mudou**: continua escutando o evento nativo
"Glue Job State Change" do EventBridge, que dispara igual não importa
quem chamou `StartJobRun` -- o desfecho do Glue na tabela de controle
é o mesmo de antes.

**A proteção contra duplicata real**: uma regra do EventBridge não
permite controlar o *nome* de uma execução do Step Functions a partir
do conteúdo do evento (só o input) -- confirmado contra pedidos de
funcionalidade ainda abertos em ferramentas como CDK e no provider AWS
do Terraform, pedindo exatamente isso. Por isso existe
`lambda/iniciar_pipeline.py`: decide um nome de execução determinístico
a partir do nome do arquivo `.zip` antes de chamar `StartExecution`. A
idempotência de verdade vem da própria API do Step Functions (fluxos
STANDARD): mesmo nome + mesmo input = sucesso idêntico, sem execução
nova; mesmo nome + input diferente = `ExecutionAlreadyExists` -- os
dois casos são tratados como sucesso por essa Lambda, nunca como erro.

Defensivo, não a causa raiz corrigida: o branch do Glue tem `Retry` em
`Glue.ConcurrentRunsExceededException` (dois arquivos *diferentes*
colidindo no limite de concorrência, cenário distinto do que motivou
isso) -- espera e tenta de novo em vez de falhar a execução.

**Confirmado contra documentação oficial da AWS** (mesma exigência já
aplicada ao protótipo do Glue Data Quality): as ações IAM exigidas pelo
padrão `.sync` do Glue (`glue:StartJobRun`, `glue:GetJobRun`,
`glue:GetJobRuns`, `glue:BatchStopJobRun`, sem controle por recurso --
o Glue não suporta isso nessas ações), o formato do nome de erro pra
`Retry`/`Catch` (`Glue.ConcurrentRunsExceededException`), e as regras
de nome da API `StartExecution` (até 80 caracteres, sem barra nem
outros caracteres especiais).

**Limite que persiste**: isto orquestra só a Bronze. Quando Silver/Gold
existirem, a mesma state machine ganha mais estados em sequência depois
do Parallel -- a base pra isso já está pronta, só não há camada
seguinte pra encadear ainda.

## Atualidade e linhagem -- checagem agendada, não reativa a evento (04/10/2026)

Retomando o limite registrado na seção do Glue Data Quality: das 6
dimensões de qualidade discutidas, completude/validade/unicidade já
tinham dono (Glue Data Quality) e volume relativo já tinha
(`quality_check.py`, contra o histórico). Faltavam **atualidade** e
**linhagem** -- as duas têm uma característica em comum que nenhuma
Lambda deste pipeline tinha até aqui: são sobre a **ausência** de
alguma coisa, não sobre o conteúdo de um evento que aconteceu. Não
existe notificação nativa da AWS para "o dia útil terminou e o arquivo
não chegou" ou "uma execução começou e nunca mais apareceu na tabela de
controle" -- só um runner que procura ativamente, numa agenda fixa,
enxerga isso. Daí `lambda/checar_pipeline.py`, disparada uma vez por
dia útil por `aws_cloudwatch_event_rule.checar_pipeline_agenda`
(`schedule_expression`, não `event_pattern` -- ver
`terraform/atualidade_linhagem.tf`), cron confirmado contra a
documentação oficial da AWS (`cron(0 11 ? * MON-FRI *)`: dia-do-mês e
dia-da-semana não podem ser especificados juntos, por isso o `?`).

**Atualidade**: o dia útil imediatamente anterior a hoje (pulando fim
de semana) teve pelo menos um `unzip_dtcc` na tabela de controle? Só
olha o dia útil mais recente -- uma falha não acumula o mesmo alerta
repetido todo dia depois.

**Linhagem**: agrupando a tabela de controle por `execution_id`, toda
execução iniciada há mais de `margem_linhagem_horas` (contrato de
fonte, tempo de sobra pro Glue e as duas etapas em paralelo
terminarem) tem as 4 etapas esperadas (`unzip_dtcc`,
`glue_data_quality`, `glue_job`, `quality_check`)? Uma execução que
começa e nunca termina não aciona nenhum alerta individual -- cada
etapa isolada não "falha", ela simplesmente nunca aparece --, e só
comparar o conjunto de etapas presentes contra o esperado enxerga essa
lacuna. `trigger_bronze` (aposentada) não entra na lista -- não existe
mais em execuções novas.

As duas checagens usam o mesmo tópico SNS de `observabilidade.tf`
(mesmo canal, não importa a causa) e gravam o próprio veredito na
tabela de controle (`origem="checar_pipeline"`, ver
`athena/queries.sql`).

**Limite aceito de propósito, visível desde já**: hoje a ingestão é
manual (`scripts/ingerir_cumulative.sh`, rodado à mão -- "Agendar a
ingestão", ver README, é tarefa futura separada, ainda não construída).
Isso significa que a checagem de atualidade vai alertar em **todo dia
útil** em que ninguém rodar o script manualmente -- não é falso
positivo, é exatamente o que "atualidade" deveria significar, e serve
de pressão real pra priorizar a próxima tarefa (agendar a ingestão
elimina esse alerta, em vez de precisar silenciá-lo).

**Limite que persiste**: cobre atualidade e linhagem; **consistência
sazonal** (ex.: volume esperado variar por dia da semana ou por época
do ano, em vez de uma média simples dos últimos N dias) continua fora
do escopo -- ver "Tarefas futuras" no README.

### Extensão de eficiência: Athena em vez de varredura do S3 (04/10/2026)

Pergunta que motivou isso: agendar `checar_pipeline.py` é de fato a
melhor prática, ou dava pra ser mais eficiente? A resposta sobre o
agendamento em si: sim, é a prática certa para uma checagem sobre
*ausência* de evento (ver acima). O que não era ótimo era a
*implementação* da leitura: a Lambda listava e lia `logs/execucoes/`
objeto por objeto (`list_objects_v2` + `get_object` em loop), em vez de
usar o próprio motor de consulta que o pipeline já tem -- Athena, com a
tabela `controle_execucoes` já existente.

Reescrito para consultar via Athena (`_consultar_athena` em
`lambda/checar_pipeline.py`): `start_query_execution` -> poll
`get_query_execution` -> `get_query_results` paginado, mesmo padrão
assíncrono já usado em `scripts/configurar_athena.sh`. Isso move o
trabalho de leitura/filtragem pro motor de query (que já é otimizado
pra isso) em vez de paginar objetos S3 a mão dentro da Lambda.

Complementar a essa mudança, uma segunda checagem nova:
**`_checar_execucoes_travadas`** cruza direto com o **Step Functions**
(`states:ListExecutions`, `statusFilter="RUNNING"`) pra achar execuções
que começaram e estão `RUNNING` há mais que `margem_linhagem_horas` --
uma fonte de verdade nativa e gratuita sobre "isso está travado", que
não existia antes (a checagem de linhagem via tabela de controle
enxerga *qual etapa falta*, não necessariamente *que a execução nunca
terminou de verdade* no Step Functions). As duas checagens são
complementares, não substitutas uma da outra.

`terraform/atualidade_linhagem.tf`: timeout da Lambda subiu de 60 pra
90s (latência de rede do Athena por chamada, ~1-3s de cold start, soma
à espera do polling); novas variáveis de ambiente `DATABASE`,
`WORKGROUP`, `STATE_MACHINE_ARN`; permissões IAM novas para
`athena:StartQueryExecution`/`GetQueryExecution`/`GetQueryResults`,
`glue:GetTable`/`GetDatabase`/`GetPartitions` (Athena resolve a tabela
via Glue Data Catalog por baixo) e `states:ListExecutions`.

Testado com Athena e Step Functions mockados
(`tests/test_lambda_checar_pipeline.py`, 10 testes): confirma o ciclo
assíncrono do Athena, o alerta de execução travada além da margem, o
silêncio dentro da margem, e que a ausência de `STATE_MACHINE_ARN`
pula a checagem sem quebrar (comportamento usado antes de a state
machine existir ou em testes isolados).

## Ingestão agendada -- elimina o último passo manual (04/10/2026)

Retomando a "Tarefas futuras" registrada desde a criação da checagem de
atualidade: a ingestão (`scripts/ingerir_cumulative.sh`) era o único
passo do pipeline que ainda dependia de alguém rodar um comando à mão.
Isso tinha dois efeitos: o pipeline não era de fato "fire and forget", e
a checagem de atualidade (seção acima) ia alertar em todo dia útil em
que ninguém lembrasse de rodar o script -- não um falso positivo, mas
uma pressão real pra resolver isso.

**O que mudou**: `lambda/ingerir_cumulative.py` reimplementa a mesma
cópia servidor-a-servidor do script (API `CopyObject`, agora via boto3
em vez de `aws s3 cp`) como Lambda, disparada por
`aws_cloudwatch_event_rule.ingestao_agendada`
(`terraform/ingestao_agendada.tf`) numa agenda fixa do EventBridge --
mesmo padrão já usado pela checagem de atualidade/linhagem
(`schedule_expression`, não reage a evento, porque não existe evento
nativo para "está na hora de buscar o arquivo de hoje"). O script
manual não foi removido -- continua útil pra backfill (data/classe de
ativo específica) ou numa conta nova, antes do Terraform existir.

**Parâmetros, todos do contrato de fonte** (`config/fontes/dtcc.yaml`,
já existiam a maioria desde a extensão de generalização): `ORIGEM_BUCKET`,
`ORIGEM_PADRAO`, `ZIP_PREFIX`, `CLASSE_ATIVO_DEFAULT`, `FONTE_DEFAULT`.
Único campo novo no contrato: `ingestao_cron`.

**Alerta na falha, não só no sucesso**: diferente do Glue (que emite
"Job State Change" nativamente, permitindo uma regra separada do
EventBridge), não existe evento nativo para "a cópia entre buckets
falhou". Por isso o alerta é publicado direto do código da Lambda
(`_alertar_falha`, mesmo padrão que `quality_check.py` já usa) antes de
propagar a exceção -- o cenário mais provável de falha é o arquivo de
hoje ainda não ter sido publicado pelo DTCC no horário agendado.

**Observabilidade**: grava um registro na tabela de controle
(`origem="ingerir_cumulative"`, `status="copiado"`) a cada cópia
bem-sucedida -- não entra em `etapas_esperadas` (a checagem de
linhagem continua olhando `unzip_dtcc` em diante, ver seção acima), é
só mais um rastro consultável no Athena, simétrico ao que as outras
Lambdas já fazem.

**Limite assumido, sem solução definitiva ainda**: o horário do cron
(`ingestao_cron: "cron(0 6 ? * MON-FRI *)"`, 6h UTC) é um ponto de
partida, não um horário confirmado contra o comportamento real de
publicação do DTCC -- não documentado oficialmente. Se a Lambda
disparar antes do arquivo existir, ela falha e alerta (comportamento
esperado, não um bug) -- mas alertar com frequência é o sinal de que o
horário precisa subir. Ajustar observando a frequência real desse
alerta ao longo de algumas semanas, não antes.

Testado com S3 e SNS mockados
(`tests/test_lambda_ingerir_cumulative.py`, 5 testes): nome de arquivo
montado corretamente a partir do padrão do contrato (incluindo
sobreposição de classe de ativo/fonte/data via o evento, útil para um
invoke manual equivalente aos argumentos do script), uso da data de
hoje (UTC) quando não especificada, registro na tabela de controle, e
o alerta por SNS propagando a exceção original numa falha de cópia
(sem registrar sucesso nesse caso).

## Consistência sazonal -- comparar contra o mesmo dia da semana (04/10/2026)

Última lacuna registrada na taxonomia de qualidade discutida desde a
adoção do Glue Data Quality: a checagem de volume relativo
(`_media_historica`, dentro de `lambda/quality_check.py`) comparava o
volume de hoje contra uma média simples que mistura todos os últimos
`DIAS_HISTORICO` dias corridos, sem distinguir dia da semana. Para um
feed financeiro isso é uma limitação real -- segunda-feira acumula o
fim de semana, fim de mês/trimestre tem padrão próprio -- e misturar
tudo numa única média tanto pode disparar falso alerta (uma
segunda-feira normal comparada contra dias de meio de semana) quanto
mascarar uma queda real (uma queda de segunda-feira dissolvida na média
de dias "normais").

**O que mudou**: nova função `_escolher_baseline`, chamada no lugar da
chamada direta a `_media_historica`. Tenta primeiro a baseline sazonal
-- `_media_historica(bucket, dias=SEMANAS_HISTORICO_SAZONAL * 7,
mesmo_dia_semana=True)`, que filtra a janela de dias corridos pra só
considerar os que cairam no mesmo dia da semana de hoje (uma janela de
`N` semanas corridas contém exatamente `N` ocorrências de qualquer dia
da semana fixo, então "semanas" e "ocorrências" são a mesma coisa aqui)
-- e cai pra média simples (`DIAS_HISTORICO`/`QUEDA_MAXIMA_TOLERADA`,
comportamento de antes) se a sazonal devolver `None` (zero ocorrências
do mesmo dia da semana na janela) ou se `CONSISTENCIA_SAZONAL` estiver
desligado pelo contrato de fonte. O rótulo usado na mensagem de alerta
(`"mesmo dia da semana"` vs `"dias corridos"`) deixa explícito, a quem
for investigar o alerta, qual baseline foi de fato usada.

**Mantido parametrizável, não cravado**: dois campos novos no contrato
de fonte (`config/fontes/dtcc.yaml`) -- `consistencia_sazonal` (liga/
desliga o método por fonte) e `semanas_historico_sazonal` (quantas
ocorrências do mesmo dia da semana olhar pra trás, default 4) --
distribuídos para a Lambda como `CONSISTENCIA_SAZONAL` e
`SEMANAS_HISTORICO_SAZONAL` (`terraform/quality_check.tf`), mesmo
padrão já usado por `DIAS_HISTORICO`/`QUEDA_MAXIMA_TOLERADA`. Uma
segunda fonte com volume que não varia por dia da semana (ou cujo
calendário de publicação não seja dias úteis fixos) pode desligar o
método sem tocar no código Python.

**Por que não entrar em "época do ano" também**: a lacuna original
mencionava os dois (dia da semana e época do ano), mas só a primeira
foi construída agora -- consistência por época do ano (padrão
mensal/trimestral, feriados) exigiria meses de histórico real pra
calibrar sem arriscar um limite inventado; dia da semana já é uma
melhoria concreta e verificável com poucas semanas de dado. Fica
registrado como extensão futura, não construída.

**Limite que persiste**: assim como a média simples que substitui,
inclui dias com problema no cálculo (não filtra por `status == "ok"`)
-- o mesmo limite já aceito em `_media_historica` desde a criação da
checagem de volume, que esta extensão não resolve.

Testado com S3 mockado respeitando a data real pedida em cada consulta
(`tests/test_lambda_quality_check.py`, 4 testes novos, usando um helper
`_mock_clients_por_data` diferente do `_mock_clients` das demais --
este último ignora o prefixo pedido, o que não serve pra provar
filtragem por dia da semana): a baseline sazonal é de fato a usada
quando há histórico suficiente (um cenário onde a média simples e a
sazonal dariam vereditos opostos sobre o mesmo arquivo -- só alerta se
a sazonal for a baseline usada); `CONSISTENCIA_SAZONAL=false` reverte
para a média simples nesse mesmo cenário; ausência de ocorrências
sazonais cai para a média simples automaticamente; e
`SEMANAS_HISTORICO_SAZONAL` restringe de fato a janela (ocorrências
fora dela não contaminam a média).

## Módulo Terraform reutilizável -- passo 2 da generalização (04/10/2026)

Retomando a "Tarefa futura" registrada desde o contrato de fonte
(passo 1): os recursos do Terraform que eram "um por fonte, declarado à
mão" -- Glue job, as 6 Lambdas (`unzip_dtcc`, `quality_check`,
`iniciar_pipeline`, `job_concluido`, `checar_pipeline`,
`ingerir_cumulative`), a state machine e as 4 regras do EventBridge que
disparam algo por fonte (`zip_arrived`, `glue_bronze_failed`,
`glue_bronze_concluido`, `checar_pipeline_agenda`,
`ingestao_agendada`) -- viraram `terraform/modules/fonte`, um módulo
só, instanciado uma vez por fonte real em `terraform/main.tf`.

**O que ficou FORA do módulo, compartilhado entre fontes** (continua em
`terraform/main.tf`, `dlq.tf`, `observabilidade.tf`): o bucket S3 (uma
fonte é um conjunto de prefixos dentro dele, não um bucket próprio), o
banco do Glue Data Catalog e o workgroup do Athena, o tópico SNS de
alertas, a fila SQS de eventos falhos (DLQ) e o budget. Critério usado
pra decidir o que entra no módulo e o que fica fora: um recurso que o
AWS modela como singleton por conta/laboratório (o budget, por
exemplo) ou que só pode ser criado uma vez por bucket (a notificação do
S3 pro EventBridge -- `aws_s3_bucket_notification` só aceita uma por
bucket) fica fora; o resto, que já era pensado como "um por fonte"
mesmo antes do módulo existir (Glue job, Lambdas, state machine),
entrou.

**Bug de generalização encontrado e corrigido durante a extração**: o
banco do Glue Data Catalog (`aws_glue_catalog_database`) tinha seu nome
amarrado a `local.fonte.nome` (`"${var.prefix}_${local.fonte.nome}"`) --
com uma fonte só, idêntico a um banco por laboratório; com uma segunda
fonte, teria criado um SEGUNDO banco (em vez de uma segunda tabela no
mesmo banco), exigindo migrar as tabelas existentes depois. Corrigido
antes de existir essa segunda fonte pra sentir a dor: o banco agora
chama só `replace(var.prefix, "-", "_")`, nome do recurso
`aws_glue_catalog_database.lab` (era `.dtcc`) -- compartilhado de
verdade, cada fonte ganha sua própria tabela dentro dele (`dtcc_bronze`
hoje, `<fonte>_bronze` numa fonte futura), não seu próprio banco.

**Convenção de nomenclatura única dentro do módulo**: todo recurso usa
`local.nome = "${var.prefix}-${var.fonte.nome}"` como raiz (ex.: Lambda
`${local.nome}-unzip`, state machine `${local.nome}-pipeline`, regra
`${local.nome}-zip-arrived`) -- antes da extração, a nomenclatura
cravada misturava `${var.prefix}-x-${local.fonte.nome}` com
`${var.prefix}-x` sem o nome da fonte (`quality-check`,
`iniciar-pipeline`, `job-concluido`, `checar-pipeline`), o que teria
colidido entre duas fontes diferentes no mesmo laboratório -- corrigido
junto com a extração, não deixado pra quando a colisão acontecesse de
verdade.

**O que o módulo recebe de fora, em vez de criar por conta própria**
(ver `modules/fonte/variables.tf`): o contrato de fonte inteiro (`var.fonte`,
um `yamldecode` já pronto -- um campo novo no YAML fica disponível
dentro do módulo sem precisar editar `variables.tf`) e as referências
aos recursos compartilhados (bucket, banco, workgroup, tópico SNS,
fila DLQ, role do Glue). O único recurso que o módulo cria mas que
*poderia* variar por fonte no futuro é o script do Glue job
(`glue_script_source_path`, hoje sempre `glue/bronze_ingest.py` --
script já genérico, dirigido pelos argumentos do contrato, mas uma
fonte com formato de arquivo muito diferente do Cumulative poderia
precisar do seu próprio).

**O que NÃO foi feito agora, de propósito (passo 3, ainda em aberto)**:
`main.tf` instancia o módulo uma vez, à mão (`module "dtcc" { ... }`),
não com um `for_each` sobre uma lista de fontes. Automatizar isso (ler
todos os arquivos de `config/fontes/*.yaml` e instanciar o módulo pra
cada um) exigiria decidir, sem uma segunda fonte real: como a DLQ
(`dlq.tf`) colecionaria os ARNs de regra de um número variável de
módulos (hoje é uma lista fixa de 4 ARNs); se cada fonte continua
compartilhando o mesmo bucket/banco/tópico/fila ou se alguma delas
precisaria ser por fonte; e se o script de ingestão
(`lambda/ingerir_cumulative.py`, específico do padrão de nome do
Cumulative) é generalizável ou vira uma Lambda diferente por fonte.
Três decisões de design que uma segunda fonte real responderia; sem
ela, qualquer resposta agora seria uma suposição -- mesmo raciocínio já
registrado no contrato de fonte quando o passo 1 foi feito.

**Limite honesto sobre validação**: este ambiente não tem o binário do
`terraform` instalado (sem acesso de rede pra instalar), então esta
extração foi revisada à mão -- balanceamento de chaves, toda referência
`var.*`/`module.dtcc.*`/`aws_*.*` resolvida contra o que é de fato
declarado (scripts de verificação descartáveis, não comitados), e os
41 testes Python (inalterados, não dependem do Terraform) continuam
passando. **`terraform validate` e `terraform plan` ainda não foram
rodados de verdade** -- rode os dois antes de um `apply`, com atenção
redobrada: a mudança de nome dos recursos (banco do Glue Data Catalog,
Lambdas, IAM roles, regras do EventBridge -- ver nomenclatura acima)
faz o Terraform querer DESTRUIR os recursos antigos e CRIAR os novos
em vez de atualizar em lugar, caso isto já tenha sido aplicado numa
conta real antes desta extração.

## Skill de projeto `nova-fonte-dados` -- generalização via Claude Code, não só Terraform (04/10/2026)

**Correção de rumo pedida pelo usuário**: o passo 3 da generalização (ver
seção anterior) estava enquadrado como "automatizar a instanciação de
múltiplas fontes no Terraform" (`for_each` sobre `config/fontes/*.yaml`).
O usuário esclareceu que "múltiplas fontes" significava algo mais amplo:
transformar este pipeline num modelo padrão de engenharia de dados, com
agentes/skills especializadas que aplicam o padrão a uma fonte nova à
medida que a demanda aparecer -- não (só) uma feature de Terraform.

Duas perguntas em aberto foram respondidas diretamente pelo usuário:

- **Onde a skill vive**: dentro deste repositório (`.claude/skills/`,
  versionada com o código), não como skill de conta Claude (que eu havia
  recomendado) nem as duas ao mesmo tempo -- decisão explícita de manter o
  padrão junto do código que ele descreve, não espalhado entre repositório
  e conta pessoal.
- **O que a skill faz**: dada só uma URL de dados públicos gratuitos, gera
  cada etapa do pipeline automaticamente, adaptada ao contexto real da nova
  fonte -- não um questionário guiado campo a campo do contrato, nem geração
  cega sem investigar o dado real. Ou seja, a skill precisa fazer o mesmo
  trabalho de investigação que foi feito à mão pra DTCC (buscar a URL, baixar
  uma amostra real, inferir schema/cadência/regras de qualidade a partir do
  dado, não da descrição) antes de gerar qualquer arquivo.

**O que foi criado**: `.claude/skills/nova-fonte-dados/SKILL.md`, uma skill
de projeto (carregada só por quem abre este repositório com o Claude Code,
diferente do plugin `aws-core`, que é instalado por máquina/conta e listado
em `.claude/settings.json`). O arquivo documenta:

- Pré-leitura obrigatória (quais arquivos do padrão existente ler antes de
  gerar qualquer coisa -- contrato de referência, módulo Terraform, Lambdas
  genéricas, convenção de testes).
- Procedimento passo a passo: investigar a URL e o dado real primeiro (nunca
  assumir estrutura pela descrição), nomear a fonte, decidir o mecanismo de
  ingestão caso a caso (não forçar o padrão S3→S3 da DTCC quando a origem
  real é HTTP comum ou uma API), decidir se `glue/bronze_ingest.py` serve
  como está ou precisa de um script novo, escrever o contrato YAML, instanciar
  `module "<nome>"` em `terraform/main.tf`, atualizar a lista de ARNs em
  `terraform/dlq.tf`, escrever testes pytest seguindo a convenção existente,
  revisar o Terraform à mão (mesma limitação sem binário `terraform`, ver
  seção anterior) e documentar as suposições assumidas.
- Uma lista explícita de "quando perguntar ao usuário em vez de assumir"
  (nome ambíguo, falta de dado real suficiente, autenticação necessária,
  qualquer coisa irreversível como rodar `apply`) -- pra não degenerar nem
  num questionário longo (o que o usuário rejeitou) nem numa geração cega
  sem nenhum checkpoint humano nas decisões que de fato importam.
- O que fica deliberadamente fora de escopo: Silver/Gold, `for_each`
  automático sobre múltiplas fontes (continua sem uma segunda fonte real
  pra validar as três perguntas de design já registradas acima), aplicar
  Terraform de fato, e sazonalidade por época do ano.

**Por que repo-local e não conta**: skill de conta valeria pra qualquer
projeto Claude Code do usuário, mas o padrão que ela replica -- contrato
YAML com estes campos exatos, módulo `terraform/modules/fonte` com esta
interface, estas 6 Lambdas genéricas -- só existe neste repositório. Uma
skill de conta teria que reimplementar ou referenciar esse padrão de algum
jeito; mantê-la dentro do repo garante que ela sempre lê a versão real do
padrão (e evolui junto, via o mesmo histórico de commits) em vez de uma
cópia que pode desatualizar.

**Limite assumido**: esta skill nunca foi executada de ponta a ponta contra
uma segunda fonte real nesta sessão -- é a mesma situação dos passos 1 e 2,
decisões de design tomadas por raciocínio cuidadoso, sem uma fonte real pra
validar contra a prática. A primeira vez que for usada deve ser tratada como
um teste do próprio procedimento, não só da fonte nova.
