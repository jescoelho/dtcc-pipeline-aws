# Passo 8b — Revisão de adequação (juiz + aplicador)

Pergunta que o passo responde: **a infraestrutura gerada é adequada ao
problema deste pipeline** (volume, frequência, SLA, retenção, criticidade),
ou está super/subdimensionada ou com risco? Roda depois do passo 8 e antes
do 9 (a documentação registra o resultado).

## Desenho (por que assim)

| Prática agêntica | Como é aplicada |
|---|---|
| Separação de funções | `juiz-adequacao` (somente leitura) e `aplicador-adequacao` (edita) são subagentes distintos, cada um em contexto novo. Quem corrige não se aprova. |
| Privilégio mínimo | Juiz: sem Edit/Write/Bash. Aplicador: sem acesso a skills AWS; **hook `PreToolUse` no frontmatter** (`.claude/hooks/guarda_aplicador.py`, teste: `python .claude/hooks/test_guarda_aplicador.py`) restringe Edit/Write a `.claude/outputs/<nome>/` (fora de `.revisao/`) e Bash a pytest e `validar_terraform.py`; nada na AWS. |
| Contrato estruturado | Handoff por arquivos JSON versionados em `OUT_DIR/.revisao/rodada-<n>/` (esquema no agente), não por prosa. |
| Humano no ponto irreversível | Portão entre juiz e aplicador: o usuário aprova achado a achado. `decisao_de_arquitetura` nunca é aplicada automaticamente. |
| Reverificação independente e cega | A rodada seguinte usa um juiz NOVO, que vê o código atual e a lista de achados anteriores, mas não o log do aplicador. |
| Laço limitado | Máx. **2 rodadas**. Se restar achado de severidade alta, reporte ao usuário; não insista. |
| Verificação mecânica ≠ julgamento | O aplicador roda pytest/validar_terraform; o juízo de qualidade é só do juiz. |
| Rastreabilidade | Tudo em `.revisao/` (brief, achados, aprovados, log) e resumo em `docs/ADEQUACAO.md`. |

## Procedimento do orquestrador (você, no contexto principal)

1. **Brief.** Escreva `OUT_DIR/.revisao/brief.md` com: fatos medidos na
   amostra (bytes/dia, linhas/dia, nº de colunas, variação por dia da
   semana — vêm do `perfilar_csv.py`), frequência de publicação, janela de
   retenção da origem; e requisitos **assumidos**, rotulados como tal
   (latência tolerada, retenção desejada, criticidade — default
   `padrão`). Se criticidade ou SLA mudarem o veredito, pergunte ao usuário
   com `AskUserQuestion` (decisão dele, não dado).
2. **Juiz, rodada 1.** `Agent(subagent_type="juiz-adequacao")` com apenas
   o caminho de `OUT_DIR`. Grave a resposta **verbatim** em
   `rodada-1/achados.json` (se não for JSON válido, peça de novo uma vez).
3. **Portão humano.** Apresente achados em tabela curta (id, componente,
   veredito, severidade, ação). `AskUserQuestion` multiSelect com os
   `mudanca_de_codigo` a aplicar; liste à parte os
   `decisao_de_arquitetura` com os trade-offs para o usuário decidir.
   Grave `rodada-1/achados-aprovados.json`. Nenhum aprovado → pule o 4.
4. **Aplicador.** `Agent(subagent_type="aplicador-adequacao")` com
   `OUT_DIR` e o caminho do arquivo de aprovados. Grave o JSON de retorno
   em `rodada-1/aplicacao.json`.
5. **Juiz, rodada 2 (contexto novo).** Passe `OUT_DIR` e o
   `achados.json` anterior; **não** passe `aplicacao.json`. Itens
   `nao_resolvidos` ou novos de severidade alta → relate ao usuário; não
   inicie rodada 3.
6. **Registro.** Escreva `OUT_DIR/docs/ADEQUACAO.md`: brief, veredito por
   componente, o que foi aplicado, o que ficou por decisão humana, o que o
   juiz não pôde determinar. Dê o resumo no relato final (passo 9).

## Gate do passo
- `achados.json` válido em cada rodada executada; nenhuma mudança fora de
  `OUT_DIR`; aplicados ⊆ aprovados; `pytest` e `validar_terraform` após a
  aplicação; `ADEQUACAO.md` existe.

## Calibração do juiz (rode ao alterar o agente ou a rubrica)
Um juiz que aprova tudo é inútil. Cenários que ele precisa discriminar,
por motivos diferentes:
- **Super**: o brief real do `ice_ticker` (40 KB/dia) → deve questionar o
  Glue/Step Functions e reconhecer o que é justificável.
- **Sub**: o mesmo código com brief de 50 GB/dia → deve apontar Glue de 2
  workers, `quality_check` com 1024 MB/60 s, e leitura integral em Lambda.
- **Ok**: brief compatível → poucos achados, só de baixa severidade.
Registre o resultado em `catalogo.md` (seção de lições).
