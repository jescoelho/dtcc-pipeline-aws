---
name: nova-fonte-dados
description: Use esta skill quando o usuário pedir para aplicar o modelo padrão deste pipeline a uma nova fonte de dados públicos, dado apenas uma URL (ex.: "cria um pipeline pra esta fonte: <url>", "aplica o padrão do repo nesse dataset", "onboarding de fonte nova"). Gera o contrato de fonte, a instância do módulo Terraform, a lógica de ingestão/parsing adaptada e os testes, seguindo exatamente o padrão já estabelecido para a fonte DTCC neste repositório. Parametrizável via `args` (url, nome, overrides de campos do contrato) -- nada de específico de uma execução fica cravado no corpo da skill.
---

# Nova fonte de dados: do URL ao pipeline

## Objetivo

Dado **apenas uma URL** que disponibiliza dados públicos e gratuitos, gerar
automaticamente todas as peças de uma nova instância do pipeline deste
repositório, seguindo o "modelo padrão" já construído e documentado para a
fonte DTCC — sem o usuário precisar responder um questionário detalhado: a
skill investiga a URL e os dados reais para inferir o contrato, e só
pergunta quando a resposta certa não pode vir dos dados (ver "Quando
perguntar").

**Não generalize demais.** Cada execução produz UMA nova instância do
padrão — não um framework multi-fonte genérico (`for_each` automático
sobre todas as fontes é o "passo 3", deliberadamente não feito, ver
`docs/DECISOES.md`).

## Papel deste arquivo

Este `SKILL.md` é só o orquestrador: diz o que fazer em cada passo e pra
onde ir buscar o "como". Nenhum parâmetro, procedimento detalhado ou
trecho de código mora aqui — fica em arquivos separados, lidos só quando o
passo correspondente precisar:

| Arquivo | Conteúdo | Quando ler |
|---|---|---|
| `references/pre-leitura.md` | Lista do que ler no repositório antes de gerar qualquer coisa | Antes do passo 1 |
| `references/parametros.md` | Entrada da execução, caminhos do padrão, recursos Terraform compartilhados, defaults de contrato | Antes do passo 1; consultado de novo nos passos 3, 5 e 6 |
| `references/procedimento.md` | O detalhe de cada um dos 9 passos abaixo | Ao executar cada passo |
| `references/mecanismo-ingestao.md` | Os três casos de mecanismo de ingestão e como decidir qual se aplica | No passo 3 |
| `assets/modulo-instancia.tf.example` | Template do bloco `module` do Terraform | No passo 6 |
| `scripts/validar_terraform.py` | Checagem heurística de balanceamento e referências (só executar, não precisa ler o código) | No passo 8 |
| `scripts/baixar_listagem.py` | Ferramenta geral parametrizável: baixa arquivos de uma origem HTTP com listagem de diretório (subpasta/ano/mês), idempotente; `--help` mostra os parâmetros | Passo 1 (amostra real) e backfill, quando a origem for uma listagem de diretório; ver `references/mecanismo-ingestao.md` |

## Procedimento (visão geral — detalhe em `references/procedimento.md`)

1. **Investigar a URL e os dados reais** — nunca assumir estrutura pela
   descrição; baixar uma amostra real antes de decidir qualquer campo do
   contrato.
2. **Escolher o nome da fonte** — a partir de `nome`, se passado, senão da
   URL/organização publicadora.
3. **Decidir o mecanismo de ingestão** — ver `references/mecanismo-ingestao.md`.
4. **Decidir se o script Glue padrão serve**, ou se o formato exige um novo.
5. **Escrever o contrato** `CONTRATOS_DIR/<nome>.yaml`.
6. **Instanciar o módulo Terraform** a partir de
   `assets/modulo-instancia.tf.example`.
7. **Escrever os testes** das Lambdas novas/modificadas.
8. **Validar o Terraform** com `scripts/validar_terraform.py` (heurístico —
   não substitui `terraform validate`/`plan`, que o usuário ainda precisa
   rodar localmente antes de qualquer `apply`).
9. **Documentar** em `README.md`/`DECISOES_MD`.

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
