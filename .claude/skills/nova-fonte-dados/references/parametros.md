# Parâmetros da skill `nova-fonte-dados`

Leia este arquivo antes do passo 1 do procedimento (ver `SKILL.md`). Ele
reúne tudo que varia de execução para execução ou que pode mudar junto com
o repositório — a ideia é que nenhum caminho, nome de recurso ou valor
numérico fique cravado no meio da prosa dos passos, só referenciado pelo
nome a partir daqui.

## Entrada da execução

| Parâmetro | Obrigatório | Default | Descrição |
|---|---|---|---|
| `url` | sim | — | URL da fonte de dados públicos a investigar. |
| `nome` | não | inferido da URL/organização publicadora no passo 2; perguntar ao usuário se ambíguo | Nome curto da fonte (minúsculas, sem espaços). Vira `<CONTRATOS_DIR>/<nome>.yaml` e o nome da instância do módulo Terraform. |
| overrides de qualquer campo do contrato (ex. `dias_historico=14`, `unicidade_minima=0.95`, `consistencia_sazonal=false`) | não | ver "Defaults de contrato" abaixo | Sobrescreve, só para aquele campo, o que o passo 1 inferiria a partir do dado real. Use quando o usuário já souber algo que a skill não teria como inferir sozinha. |
| `pular_ingestao` | não | `false` | Se `true`, não gera uma Lambda de ingestão agendada — assume que o usuário vai alimentar a fonte manualmente por enquanto. |

Estes chegam como `args` do `Skill` tool (ex. `nome=cvm unicidade_minima=0.95`),
sem precisar editar nenhum arquivo da skill.

## Caminhos do padrão

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

## Recursos Terraform compartilhados

O módulo consome estes recursos **por referência** (não os cria). Os nomes
abaixo são os declarados hoje em `MAIN_TF` — releia `MAIN_TF` e confirme os
nomes reais antes de montar o bloco `module` (passo 6 do procedimento); não
copie estes valores as cegas se o arquivo tiver mudado.

| Parâmetro | Default (recurso em `MAIN_TF`, estado em 04/10/2026) |
|---|---|
| `RECURSO_BUCKET` | `aws_s3_bucket.lake` |
| `RECURSO_GLUE_ROLE` | `aws_iam_role.glue` |
| `RECURSO_GLUE_DATABASE` | `aws_glue_catalog_database.lab` |
| `RECURSO_ATHENA_WORKGROUP` | `aws_athena_workgroup.lab` |
| `RECURSO_SNS_TOPIC` | `aws_sns_topic.alertas` |
| `RECURSO_DLQ` | `aws_sqs_queue.eventos_falhos` |

## Defaults de contrato

Usados **só** quando o dado real investigado no passo 1 do procedimento não
permitir inferir algo mais específico — nunca a primeira escolha quando a
amostra real dá uma resposta melhor, e sempre vencidos por um override
explícito recebido como parâmetro de execução.

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

Ordem de precedência, do mais forte pro mais fraco: override passado como
parâmetro de execução > inferência do dado real investigado > default
desta tabela.
