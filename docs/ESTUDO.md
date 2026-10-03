# Pipeline DTCC PPD, explicado em linguagem simples

Este documento explica o que foi construído até agora e por que, sem
assumir conhecimento prévio. Ele parte de onde o laboratório
`b3-pipeline-aws` (e seu módulo `src/cdc/`, de CDC sintético) chegou —
vale a pena ter aquele contexto, mas não é obrigatório.

## 1. Por que um projeto separado

O `b3-pipeline-aws` lida com um dado que **nunca muda depois de escrito**:
o pregão de ontem da B3 é definitivo. Esse projeto lida com um dado
**que muda o tempo todo** — transações de swap que podem ser corrigidas,
alteradas ou encerradas depois de criadas. São problemas de engenharia
diferentes, então ganharam repositórios diferentes — mas seguem a mesma
arquitetura (medalhão) e os mesmos princípios de teste.

## 2. A fonte: PPD (Public Price Dissemination) do DTCC

O DTCC é uma infraestrutura de mercado que, entre outras coisas, mantém o
repositório de transações de derivativos exigido por lei nos EUA
(Dodd-Frank). Parte desse dado é tornada pública, de forma anonimizada,
em https://pddata.dtcc.com/ppd/cftcdashboard. O relatório **Cumulative**
consolida todas as transações de um dia, por classe de ativo — usamos
**Rates** (derivativos de taxa de juros), por ser a mais próxima do
trabalho de uma mesa/time de Tesouraria.

## 3. A descoberta que mudou o desenho: não é um snapshot, é um log de eventos

A expectativa inicial era que o Cumulative fosse parecido com o COTAHIST:
uma foto do dia, uma linha por transação. Abrindo o arquivo real
(02/10/2026, 26.840 linhas), isso não é verdade. A coluna `Action type`
mostra que cada linha é um **evento**:

| Action type | Significado |
|---|---|
| `NEWT` | Nova transação |
| `MODI` | Modificação de uma transação existente |
| `CORR` | Correção |
| `TERM` | Encerramento (a transação deixou de existir) |
| `EROR` | Erro/anulação |
| `REVI` | Revisão |

E quando uma linha não é `NEWT`, ela aponta para a transação original
através da coluna `Original Dissemination Identifier`. No arquivo real,
uma mesma transação chegou a acumular 12 eventos num único dia:

```
5598983937000000601                          NEWT  19:00:00
5598983979000000301  -> 5598983937...000601   MODI  19:00:04
5598984059000000201  -> 5598983937...000601   MODI  19:00:07
5598983938000000701  -> 5598983937...000601   MODI  19:00:24
... (mais 8 eventos ao longo do dia)
```

Isso significa que **26.840 linhas não são 26.840 transações** — são
26.840 *eventos*, que juntos descrevem um número menor de transações
distintas, cada uma com uma história. Para saber "quantas transações de
swap existem e qual é o estado de cada uma agora", é preciso processar
essa história, não só contar linhas.

## 4. Por que isso é CDC (Change Data Capture) de verdade

No laboratório `b3-pipeline-aws/src/cdc/`, CDC foi ensinado com duas
"fotos" completas de uma tabela, comparadas para descobrir o que mudou
(diff de snapshots — a forma mais simples e mais cara de fazer CDC). Esse
arquivo do DTCC mostra a alternativa mais eficiente: **CDC baseado em
log de eventos**. Em vez de comparar fotos, cada mudança já chega como um
evento explícito (`MODI`, `TERM`...), referenciando o que ela está
mudando. É o mesmo princípio por trás de ferramentas como AWS DMS ou
Debezium, que leem o log de transação de um banco em vez de comparar
tabelas inteiras — só que aqui o "log" é entregue como um arquivo diário,
em vez de um stream contínuo.

## 5. Bronze: guardar os eventos como chegaram

`src/dtcc/bronze.py` lê o CSV e grava em Parquet, sem converter nenhum
valor — mesmo princípio da Bronze do projeto B3 (ver lá, seção 8 do
`ESTUDO.md` daquele repositório), aplicado a um formato diferente:

- **COTAHIST** é largura fixa: recortar as posições certas já é uma
  forma de interpretação, então a Bronze de lá guarda a linha crua
  inteira, sem nem separar em colunas.
- **CSV do DTCC** já vem com colunas delimitadas pelo próprio arquivo
  (a vírgula e o cabeçalho já definem a estrutura) — dividir em colunas
  aqui não é interpretar valores, é só reconhecer uma estrutura que o
  arquivo já declara. Por isso a Bronze deste projeto guarda as 110
  colunas originais, mas sem converter nenhuma (tudo string, inclusive
  campos vazios).

Em ambos os casos, a regra é a mesma: **a Bronze não decide o que o dado
significa** — ela só garante que, se algo der errado na interpretação
mais adiante, dá para refazer a partir do dado original, sem precisar
baixar o arquivo de novo.

**Validado contra dado real:** o arquivo completo (26.840 linhas, 110
colunas, ~16 MB) foi ingerido em 2,1 segundos, resultando em ~1,9 MB de
Parquet — compressão de ~8x, mesma ordem de grandeza observada no projeto
B3. `tests/test_dtcc.py` usa um recorte real de 300 linhas (não dado
sintético) para confirmar que nenhuma linha é perdida e nenhum valor é
alterado na ingestão.

## 6. O que vem a seguir: Silver

A Bronze guarda a história completa de eventos. A Silver vai precisar:

1. Agrupar os eventos pela transação original (`Original Dissemination
   Identifier`, ou o próprio `Dissemination Identifier` quando o evento
   é `NEWT` e não referencia nada).
2. Ordenar cada grupo por `Event timestamp`.
3. Aplicar os eventos em ordem — a última versão de cada campo "ganha"
   (padrão *last-write-wins*, o mesmo mecanismo central de CDC) — exceto
   quando o evento é `TERM`, que marca a transação como encerrada.
4. Produzir duas saídas: uma tabela com o **estado atual** de cada swap
   (uma linha por transação), e outra com o **histórico completo**
   preservado (para auditoria — nunca se descarta o que realmente
   aconteceu, só se resume).

Esse é exatamente o próximo passo do roteiro.

## Glossário rápido

- **Evento de disseminação:** cada linha do Cumulative; representa uma
  ação sobre uma transação (criação, mudança, encerramento), não a
  transação em si.
- **CDC baseado em log:** capturar mudanças lendo uma sequência de
  eventos já publicada (ou o log de transação de um banco), em vez de
  comparar o estado completo de antes e depois.
- **Last-write-wins:** ao reconstruir o estado atual de um registro a
  partir de uma sequência de eventos, o valor de cada campo é o do
  evento mais recente que o alterou.
