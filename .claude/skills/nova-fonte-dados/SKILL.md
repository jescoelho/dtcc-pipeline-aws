---
name: nova-fonte-dados
description: Aplica o modelo padrão deste pipeline (DTCC) a uma nova fonte de dados públicos a partir de uma URL: investiga a origem e os dados reais, infere o contrato de fonte e gera, em .claude/outputs/<nome>/, o pipeline autocontido (ingestão agendada, Glue/Data Quality, Terraform, testes, docs). Use quando o usuário pedir "cria um pipeline pra esta fonte: <url>", "aplica o padrão do repo nesse dataset" ou "onboarding de fonte nova". Args opcionais: url, nome, pular_ingestao, overrides de campos do contrato.
---

# Nova fonte de dados: do URL ao pipeline

## Objetivo

Dado **apenas uma URL** que disponibiliza dados públicos e gratuitos, gerar
o **pipeline completo** dessa fonte — o mesmo escopo já construído para a
DTCC (ingestão agendada → Bronze com Data Quality → checagens de volume,
atualidade e linhagem → alertas, tudo em Terraform, com testes e
documentação) — sem o usuário precisar responder um questionário: a skill
investiga a URL e os dados reais para inferir o contrato, e só pergunta
quando a resposta certa não pode vir dos dados (ver "Quando perguntar").

A skill **aprende com o uso**: ao fim de cada execução (passo 10) ela
propõe extrair para ferramentas reutilizáveis o que for genérico (grava
só com aprovação do usuário), para que a
próxima fonte comece mais adiantada.

**Não generalize demais.** Cada execução produz UM pipeline — não um
framework multi-fonte genérico (`for_each` automático sobre todas as
fontes é o "passo 3", deliberadamente não feito, ver `docs/DECISOES.md` do repositório).

## Onde a saída é gerada

Tudo que a skill gera vai para **`.claude/outputs/<nome>/`**, uma pasta
**autocontida** (contrato, Lambdas, Glue, Terraform com o módulo, testes,
Athena, `README.md`, `docs/DECISOES.md`) que espelha o layout do
repositório. A skill **não altera** o repositório principal (nem o
módulo `terraform/modules/fonte`, nem as Lambdas, nem `docs/`): ela os lê
como **referência do modelo** e copia/adapta para dentro da pasta de
saída. Exemplo completo e já validado: `.claude/outputs/ice_ticker/`. Se
`.claude/outputs/<nome>/` já existir, pergunte antes de sobrescrever.

## Papel deste arquivo

Este `SKILL.md` é só o orquestrador: diz o que fazer em cada passo e pra
onde ir buscar o "como". Nenhum parâmetro, procedimento detalhado ou
trecho de código mora aqui — fica em arquivos separados, lidos só quando o
passo correspondente precisar:

| Arquivo | Conteúdo | Quando ler |
|---|---|---|
| `references/catalogo.md` | O que a skill já sabe fazer: ferramentas, exemplos concluídos, candidatos e lições | **Antes do passo 1** (reutilizar antes de escrever à mão) |
| `references/revisao-adequacao.md` | Passo 8b: juiz e aplicador em subagentes separados (`.claude/agents/`), portão humano, laço limitado, calibração | Passo 8b |
| `references/evolucao.md` | Como a skill evolui: critérios para extrair ferramentas, regra de duas, registro de mudanças | Passo 10 |
| `references/pre-leitura.md` | O que ler do repositório (modelo de referência), em camadas: essencial agora vs. sob demanda | Antes do passo 1 |
| `references/parametros.md` | Entrada da execução, caminhos do modelo e da saída, recursos Terraform compartilhados, defaults de contrato | Antes do passo 1; consultado de novo nos passos 3, 5 e 6 |
| `references/procedimento.md` | O detalhe de cada passo, com o gate (critério verificável) de cada um | Ao executar cada passo — leia só a seção do passo corrente |
| `references/mecanismo-ingestao.md` | Os casos de mecanismo de ingestão e como decidir qual se aplica | No passo 3 |
| `assets/modulo-instancia.tf.example` | Template do bloco `module` do Terraform | No passo 6 |
| `scripts/validar_terraform.py` | Checagem heurística de balanceamento e referências (só executar, não precisa ler o código) | No passo 8 |
| `scripts/smoke_test.py` | Autoteste dos scripts (`--help` + dados sintéticos); rode após alterar qualquer script | Passo 10 |
| `scripts/sondar_origem.py` | Sonda uma URL antes de baixar: `robots.txt`, tipo, endpoints de API (lidos dos scripts da página), links/menções de termos | Passo 1, sempre |
| `scripts/perfilar_csv.py` | Perfila amostras CSV: formato, id candidato, domínio, colunas ruins p/ Parquet, volume por dia da semana (evidência sazonal) | Passos 1 e 5 |
| `scripts/baixar_listagem.py` | Ferramenta geral parametrizável: baixa arquivos de uma origem HTTP com listagem de diretório (subpasta/ano/mês), idempotente; `--help` mostra os parâmetros | Passo 1 (amostra real) e backfill, quando a origem for uma listagem de diretório; ver `references/mecanismo-ingestao.md` |

Scripts: execute-os por `${CLAUDE_SKILL_DIR}/scripts/<nome>.py` (nunca por caminho relativo ao cwd) e
nunca leia o código deles para usá-los — `--help` basta.

## Procedimento (visão geral — detalhe em `references/procedimento.md`)

1. **Investigar a URL e os dados reais** — nunca assumir estrutura pela
   descrição; baixar uma amostra real antes de decidir qualquer campo do
   contrato.
2. **Escolher o nome da fonte** — a partir de `nome`, se passado, senão da
   URL/organização publicadora.
3. **Decidir o mecanismo de ingestão** — ver `references/mecanismo-ingestao.md`.
4. **Decidir se o script Glue padrão serve**, ou se o formato exige um novo.
5. **Escrever o contrato** `OUT_DIR/config/fontes/<nome>.yaml`.
6. **Montar o Terraform autocontido** em `OUT_DIR/terraform/` (módulo +
   raiz + instância) a partir do modelo.
7. **Escrever os testes** das Lambdas, em `OUT_DIR/tests/`, e rodá-los.
8. **Validar o Terraform** com `scripts/validar_terraform.py` (heurístico —
   não substitui `terraform validate`/`plan`, que o usuário ainda precisa
   rodar localmente antes de qualquer `apply`).
8b. **Revisão de adequação** — subagente `juiz-adequacao` (somente leitura)
    julga a infra contra o problema real; o usuário aprova; subagente
    `aplicador-adequacao` aplica só o aprovado; novo juiz reverifica (máx. 2
    rodadas). Ver `references/revisao-adequacao.md`.
9. **Documentar** em `OUT_DIR/README.md` e `OUT_DIR/docs/DECISOES.md`.
10. **Evoluir a skill** — inventariar o que foi feito à mão, extrair o que
    é genérico para `scripts/`/`assets/`, registrar no catálogo e no
    registro de mudanças (ver `references/evolucao.md`). Obrigatório em
    toda execução.

## Invariantes (valem em todos os passos; não negociáveis)

- O repositório principal é **somente leitura**; tudo vai para `OUT_DIR`.
- Nada de `terraform apply`, nem qualquer escrita na conta AWS.
- Nunca contornar bloqueio da origem: respeitar `robots.txt`, parar em
  401/403/429, não disfarçar User-Agent.
- Dado real antes de lógica de parsing; amostras da origem não entram na
  skill (só recortes pequenos como fixture de teste em `OUT_DIR/tests/`).
- Todo valor assumido (default ou palpite) é comentado no contrato e em
  `OUT_DIR/docs/DECISOES.md`, e repetido ao usuário no resumo final.

## Definição de pronto (checar antes de dizer "concluído")

1. `python -m pytest` em `OUT_DIR` passou (cole a contagem no resumo).
2. `validar_terraform.py` sem problemas (ou cada problema explicado).
3. Passo 8b executado (ou dispensado pelo usuário, registrado) e `docs/ADEQUACAO.md` existe.
4. Contrato sem campo órfão e sem valor arbitrário; suposições comentadas.
5. `README.md` e `docs/DECISOES.md` existem em `OUT_DIR`.
6. Passo 10 executado (ou "nada a extrair" registrado) e `smoke_test.py` passa.
7. Resumo final ao usuário: o que foi gerado, o que foi **assumido**, o que
   **não foi verificado** (`terraform validate/plan`, autorização da fonte,
   acesso da Lambda à origem a partir de IP de nuvem).

Se um item não puder ser cumprido, diga qual e por quê — não declare pronto.

## Quando perguntar ao usuário

Quando a decisão for do usuário, **use a ferramenta `AskUserQuestion`**
(opções claras, a recomendada primeiro) — não pergunte em texto corrido e
não adivinhe. Pergunte quando:

- O nome da fonte não for óbvio e `nome` não foi passado como parâmetro.
- A URL não deixar claro qual arquivo/endpoint é o dado "atual" (várias
  versões, nenhuma marcada como mais recente).
- A origem exigir autenticação/credenciais que o usuário precisa fornecer.
- Os termos de uso da origem restringirem copiar/redistribuir/acesso
  automatizado, ou a origem exigir aceite de termos: confirme que o
  usuário tem autorização antes de baixar em escala ou automatizar.
- Faltarem dados reais suficientes para inferir `coluna_id`,
  `unicidade_minima` ou `colunas_obrigatorias` com confiança mínima, e
  nenhum override para esses campos foi passado.
- `.claude/outputs/<nome>/` já existir.
- A decisão envolver algo irreversível ou caro de desfazer no ambiente real
  (ex.: o usuário pedir para já rodar `terraform apply`).

Fora desses casos, tome a decisão mais razoável na ordem de precedência de
`references/parametros.md` (override > inferência > default), documente a
suposição por escrito e sinalize claramente ao usuário o que foi assumido,
em vez de travar o trabalho pedindo confirmação pra cada detalhe.

## Fora de escopo desta skill

- Camadas Silver/Gold (o pipeline hoje só tem Bronze).
- Automatizar a instanciação de múltiplas fontes via `for_each` (passo 3 da
  generalização, deliberadamente não feito — ver `docs/DECISOES.md` do repositório).
- Rodar `terraform apply` de fato, ou qualquer alteração real na conta AWS.
- Compartilhar infraestrutura entre pipelines: cada URL tem seu pipeline
  próprio e independente (bucket, banco, SNS, DLQ e tabela de controle
  próprios); só é preciso um `prefix` diferente por pipeline.
- Consistência sazonal por época do ano (mensal/trimestral/feriados).
