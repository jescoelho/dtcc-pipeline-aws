# Checklist: validar o que a IA desenvolveu

**Regra de ouro:** o que você não testou, você não sabe se funciona.
Se tiver pouco tempo, nunca pule os passos **1, 3 e 6**.

## Depois que a IA terminar

- [ ] **1. Salve o estado.** Faça `git commit` antes de mexer em qualquer coisa. Assim você sempre pode voltar.
- [ ] **2. Peça o mapa.** Cole: "Liste todos os arquivos criados, uma linha para cada: o que faz e quem chama quem. Diga qual é o arquivo por onde tudo começa." Leia o mapa, não os arquivos.
- [ ] **3. Rode com um exemplo pequeno em que você sabe a resposta certa.** Se o resultado não bater, pare aqui e conserte antes de continuar.
- [ ] **4. Rode os verificadores automáticos** (`ruff` e `mypy`). Eles pegam erros bobos, importações inexistentes e funções inventadas.
- [ ] **5. Peça testes e rode.** Inclua casos estranhos: vazio, valor faltando, número muito grande. Confira que a IA não alterou um teste só para ele passar.
- [ ] **6. Olhe o que mudou** (`git status` e `git diff --stat`). Leia só os arquivos com regra ou cálculo; os de apoio (configuração, mensagens) podem passar. Desconfie de:
  - `try/except` que esconde erro;
  - arquivos alterados fora do que você pediu;
  - testes removidos ou marcados com `skip`;
  - valores fixos no código.
- [ ] **7. Adicione as docstrings** seguindo o `GUIA_DOCSTRINGS.md`. Cole: "Adicione docstrings seguindo o GUIA_DOCSTRINGS.md, sem alterar nenhuma lógica." Depois confira no `git diff` que só docstrings mudaram. Nas funções críticas, veja se o que a docstring promete bate com o resultado do passo 3.
- [ ] **8. Peça uma segunda opinião numa conversa nova.** Cole: "Aponte o que pode estar errado neste código." Sem o histórico, a IA não herda as suposições de quem escreveu.
- [ ] **9. Registre no `NOTAS.md`:** o que foi feito, o que foi testado, o que ficou pendente.
- [ ] **10. Limpe o que sobrou.** Pergunte: "Quais arquivos não são usados por nenhum outro?" A IA costuma deixar arquivos extras.

## Se algo falhar

1. Peça: "Rode de novo mostrando uma mensagem em cada etapa, para eu ver onde quebra."
2. Conserte **um arquivo por vez** e rode de novo a cada correção.
3. Se piorou, volte ao último commit e tente de outra forma.

## Em projeto que continua por vários dias

**Ao retomar:**
- [ ] Peça um resumo do estado atual: o que está pronto, pela metade e quebrado.
- [ ] Rode tudo antes de mexer. Se algo já falha, você descobre antes de misturar com mudança nova.
- [ ] Peça um resumo do que mudou desde o último commit.

**Durante:**
- [ ] Faça um commit pequeno a cada passo que funciona.
- [ ] Peça que a IA não refatore o que não foi pedido.

**Ao encerrar o dia:**
- [ ] Peça um resumo: o que foi feito, o que ficou pendente, qual o próximo passo e o que pode quebrar. Salve no `NOTAS.md`.
- [ ] Rode os testes de novo. Se passam, amanhã você retoma de um ponto limpo.

**Uma vez por semana:**
- [ ] Peça: "Tem código duplicado, arquivo morto ou lógica contraditória entre os arquivos?"
