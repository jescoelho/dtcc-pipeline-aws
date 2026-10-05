# Evolução da skill — o que fazer ao fim de TODA execução

A skill melhora a cada fonte nova. Este é o passo 10 do procedimento e é
**obrigatório**, inclusive quando a conclusão é "nada a extrair" (nesse caso
registre isso). Leia `references/catalogo.md` antes do passo 1 (para
reutilizar o que já existe) e este arquivo no passo 10.

## 1. Inventário

Liste o que você escreveu ou fez **à mão** nesta execução fora de templates
e scripts já existentes: comandos de investigação repetidos, trechos de
análise de dados, Lambdas/`.tf` de ingestão, parsers, queries, testes de
um padrão novo, e correções que o modelo (repositório principal) precisou.
Fonte do inventário: o histórico da própria execução. Pergunta-guia:
*"que chamadas eu repeti, ou refaria, na próxima URL?"*

## 2. Classificar cada item

| Classe | Critério | Ação |
|---|---|---|
| **A. Genérico agora** | Depois de parametrizado não contém URL, coluna, endpoint, nome de fonte ou credencial; serve a ≥2 cenários plausíveis (ex.: qualquer CSV, qualquer URL); teria poupado ≥3 chamadas manuais | Extrair já para `scripts/` ou `assets/` (seção 3) |
| **B. Esqueleto reutilizável, detalhes específicos** | A estrutura se repetiria (ex.: Lambda de ingestão HTTP com token), mas o miolo depende da origem | **Regra de duas:** não extrair na 1ª vez; registrar no catálogo como *exemplo concluído* apontando para `.claude/outputs/<nome>/` e em "Candidatos". Na 2ª ocorrência parecida, extrair o denominador comum como template |
| **C. Específico** | Só faz sentido para aquela origem | Fica só em `.claude/outputs/<nome>/`; nada na skill |

Resista a generalizar cedo demais: uma abstração tirada de um único caso
costuma estar errada. A classe B existe para isso.

## 3. Como extrair (classe A)

**Aprovação antes de gravar:** a skill não se reescreve sozinha. Antes de
criar/alterar qualquer arquivo em `scripts/`, `assets/` ou `references/`,
apresente ao usuário a lista do que extrairia (inventário + classe + arquivos
afetados) e grave só após aprovação via `AskUserQuestion`. Sem aprovação,
registre os itens como candidatos no relatório final e não toque na skill.

- Ferramenta de linha de comando em `scripts/<verbo_objeto>.py`: só
  biblioteca padrão quando possível, `argparse` com `--help`, docstring em
  português dizendo o que faz, o que **não** faz e quando usar, saída que
  mostra **evidências** (não decide pelo usuário), código de saída
  significativo.
- Respeita limites éticos/técnicos: User-Agent identificado, `robots.txt`,
  para em 401/403/429, sem contornar bloqueio, sem credenciais embutidas,
  nenhum dado da origem (amostras) copiado para dentro da skill.
- **Teste de fumaça** contra o dado/origem desta execução antes de
  registrar. Se não rodar, não entra na skill.
- Mudanças **aditivas**: não altere o comportamento de scripts existentes;
  se for necessário mudar (ou renomear/remover) algo existente, use
  `AskUserQuestion`.
- Registre: (a) uma linha na tabela de arquivos do `SKILL.md`, (b) uma
  entrada em `references/catalogo.md`, (c) o passo de
  `references/procedimento.md` que passa a usá-la, (d) uma linha no
  registro de mudanças abaixo.
- `SKILL.md` continua sendo só orquestrador: nada de detalhe de execução
  nele além de uma linha na tabela.

## 4. Lições sobre o modelo

Se a fonte revelou defeito ou limite do modelo ou dos defaults (ex.: um
default que não serviu, uma suposição da skill desmentida pelos dados),
ajuste `parametros.md`/`procedimento.md` (ou registre em "Lições" do
catálogo) com o motivo. Não edite o repositório principal.

## 5. Fechar

- Rode `scripts/smoke_test.py` (acrescente nele o teste de qualquer script novo); confira que o `SKILL.md` continua curto.
- Se o catálogo passar de ~12 itens ou houver duplicatas, consolide.
- Diga ao usuário, em poucas linhas, **o que a skill aprendeu**: o que foi
  extraído, o que ficou como candidato e por quê.

## Registro de mudanças

| Data | Fonte | Mudança | Motivo |
|---|---|---|---|
| 2026-10-05 | `ice_ticker` | + `scripts/sondar_origem.py` (classe A) | A descoberta da API de uma SPA (leitura do bundle JS, `robots.txt`, termos) levou ~8 chamadas manuais e serve a qualquer URL |
| 2026-10-05 | `ice_ticker` | + `scripts/perfilar_csv.py` (classe A) | A análise de schema/unicidade/domínio/sazonalidade foi refeita à mão duas vezes e alimenta diretamente o contrato |
| 2026-10-05 | `ice_ticker` | Lambda de ingestão HTTP com token registrada como candidata (classe B) | 1ª ocorrência; extrair o denominador comum só na 2ª origem HTTP |
| 2026-10-05 | `ice_ticker` | Novo modelo de saída (`.claude/outputs/<nome>/`, pipeline autocontido, uma pilha por URL) | Pedido do usuário; evita alterar o repositório principal |
| 2026-10-05 | avaliação da skill | + `scripts/smoke_test.py`; pré-leitura em camadas; gates por passo, invariantes e definição de pronto no `SKILL.md`; saída UTF-8 nos scripts (Windows) | Avaliação de engenharia de contexto/harness: DECISOES.md (1100+ linhas) era lido inteiro; não havia critério verificável de conclusão nem autoteste dos scripts |
