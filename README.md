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
  -> Glue Bronze  -> S3 bronze/  (linha crua, sem tipar, com proveniência)
  -> Glue Silver  -> S3 silver/  (cadeia de eventos resolvida -> estado atual por swap)
  -> Glue Gold    -> S3 gold/    (métricas de negócio, a definir)
  -> Athena (consulta direto na Gold)
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

## Roteiro de evolução

1. **Bronze** (feito) — ingestão crua do CSV Cumulative, sem converter
   tipo nenhum, particionado por arquivo de origem.
2. **Silver** — resolver a cadeia de eventos: para cada swap (agrupado
   pelo id original), aplicar os eventos em ordem de `Event timestamp` e
   produzir o estado atual (last-write-wins, descartando o histórico
   intermediário) + uma tabela de histórico completo (para auditoria).
3. **Gold** — métricas de negócio sobre o estado atual: volume por
   produto, distribuição de prazos, concentração por contraparte
   (campos exatos a definir quando a Silver estiver pronta).
4. **AWS** — Terraform + Glue, no mesmo padrão do `b3-pipeline-aws`
   (bucket próprio, IAM role do Glue, Athena workgroup, Budget).
5. **Intraday** — trocar o relatório Cumulative (diário) pelo Slice
   (publicado a cada ~15min) com polling agendado (Lambda + EventBridge),
   completando a progressão até dado de verdade quase em tempo real.

## Avisos honestos

- O schema (110 colunas) foi extraído de um arquivo real baixado em
  02/10/2026 — pode mudar se o DTCC alterar o layout; confira o header
  do CSV se usar outra data.
- Ainda não há infraestrutura AWS neste repositório — só a Bronze local,
  testada e validada contra dado real. Terraform/Glue ficam para quando
  a Silver estiver pronta.
- Este projeto é de laboratório pessoal, com dado público do DTCC — sem
  nenhuma conexão com sistemas ou infraestrutura do Itaú.
