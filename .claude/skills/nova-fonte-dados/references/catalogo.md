# Catálogo — o que a skill já sabe fazer

Leia antes do passo 1: **reutilize antes de escrever à mão.** Atualizado no
passo 10 (ver `references/evolucao.md`, que define os critérios e o
registro de mudanças).

> `.claude/outputs/` é **local e ignorado pelo git**. Nada neste catálogo
> depende de uma pasta de saída existir no clone: o que vale reutilizar está
> descrito aqui, em `scripts/`, em `.claude/agents/` ou no repositório
> principal (modelo DTCC). Pipelines já gerados, se presentes na máquina,
> são só exemplos de consulta.

## Ferramentas (`scripts/`)

| Ferramenta | Use quando | Entrada → saída | Origem |
|---|---|---|---|
| `sondar_origem.py <url>` | Primeiro contato com **qualquer** URL (passo 1) | URL → `robots.txt`, tipo (arquivo/listagem/API/SPA), endpoints candidatos lidos dos scripts da página, links e menções de termos de uso. Só GETs estáticos; nunca pede token nem chama endpoint de dados | `ice_ticker` |
| `perfilar_csv.py <arquivos…>` | Já há ≥1 amostra CSV (passos 1 e 5); melhor com vários dias | CSVs → formato, estabilidade do header, preenchimento/distintos por coluna, candidatas a `coluna_id` (único entre todos os arquivos) e a domínio, colunas ruins p/ Parquet, volume por dia da semana e evidência para `consistencia_sazonal` | `ice_ticker` |
| `baixar_listagem.py` | A origem é listagem de diretório HTTP (subpasta/ano/mês) | Base + template + regex → arquivos baixados, idempotente | anterior |
| `validar_terraform.py` | Passo 8 e após o aplicador (8b) | `.tf` + contrato → balanceamento e referências. Heurístico: aponte-o para `terraform/modules/fonte`, não para a raiz (na raiz dá falsos positivos de "campo sem uso") | anterior |
| `smoke_test.py` | Passo 10, após alterar scripts | — → ok/falha de `--help` e de testes sintéticos de `perfilar_csv`/`validar_terraform` | avaliação |

## Agentes e proteção (passo 8b, ver `references/revisao-adequacao.md`)

| Peça | Papel | Observação |
|---|---|---|
| `.claude/agents/juiz-adequacao.md` | Julga a infra contra o brief do problema; somente leitura; devolve achados em JSON | Recebe só `OUT_DIR` e o brief; na reverificação, é um juiz novo que não vê o log do aplicador |
| `.claude/agents/aplicador-adequacao.md` | Aplica só achados aprovados pelo usuário e roda pytest/validador | Sem skills AWS; nada na AWS; falha vira `bloqueado`/`revertido`, não "ajuste do teste" |
| `.claude/hooks/guarda_aplicador.py` (+ `test_guarda_aplicador.py`) | Hook `PreToolUse` do aplicador: Edit/Write só em `.claude/outputs/<nome>/` (fora de `.revisao/`) e Bash só `pytest`/`validar_terraform.py` | Falha fechada; já provado dentro do subagente (escrita fora de `OUT_DIR` bloqueada) |

**Calibração do juiz:** feita só no cenário "super" (`ice_ticker`, 40 KB/dia),
em que ele reconheceu a escala correta e achou lacunas reais de
confiabilidade. **Pendentes:** cenário "sub" (50 GB/dia) e "ok"; rode-os ao
alterar o agente ou a rubrica.

## Padrões de pipeline já resolvidos

Referência do modelo: DTCC (repositório principal) — bucket S3 público,
`CopyObject`. Um segundo padrão foi construído na fonte `ice_ticker`:

- **Ingestão HTTP com token anônimo (SPA, caso 3).** Fluxo
  `getToken → getConfig → export?date=`; só `urllib`; a data vem de
  `EXPORT_LAST_AVAILABLE_DATE` da origem (não do relógio local); resposta
  200 que não seja o CSV esperado é falha, nunca gravada; alerta por SNS;
  testes com `urlopen` mockado.
- **CSV direto, sem `.zip`:** grave em `zip_prefix` e deixe o
  `unzip_dtcc.py` aceitar `.csv` (copia sem descompactar), mantendo a state
  machine idêntica.
- **Janela de retenção na origem** (aqui, 1 ano): backfill tem prazo e o raw
  é irrecuperável — isso pesa na confiabilidade (ver lições abaixo).

## Candidatos (vistos 1 vez; extrair na 2ª ocorrência — regra de duas)

- **Lambda de ingestão HTTP**: esqueleto comum = autenticar (se preciso),
  descobrir a data, baixar com timeout, validar o formato, gravar em
  `zip_prefix`, registrar na tabela de controle, alertar por SNS. Extrair
  como template quando uma 2ª origem HTTP aparecer.
- **Teste padrão de ingestão HTTP** (feliz, data fora da janela, resposta 200
  que não é o formato, 403, data mal formada).
- **Correções recorrentes do modelo** achadas pelo juiz no 1º ciclo de 8b.
  Se a 2ª fonte repetir, levar para o `modulo-instancia.tf.example` em vez
  de depender do juiz:
  - alerta de execução `FAILED/TIMED_OUT/ABORTED` da state machine
    (EventBridge `Step Functions Execution Status Change` → SNS);
  - `Retry` para erros transientes de Lambda nas Tasks;
  - `on_failure` na Lambda de ingestão (a DLQ do alvo do EventBridge só
    cobre falha de **entrega**, não erro/timeout da função) e timeout da
    Lambda maior que a soma dos timeouts HTTP do código;
  - alarme na DLQ;
  - checagem de atualidade que respeite `publica_fim_de_semana` (o modelo
    herdado do DTCC só olha o dia útil anterior);
  - versionamento/`force_destroy=false` no bucket quando a origem não devolve
    o passado.

## Lições sobre o modelo

- O modelo DTCC compartilha `logs/execucoes/` entre fontes sem separá-las.
  Como cada URL tem pipeline próprio (bucket e tabela de controle
  próprios), isso não é problema; não adicione filtros por fonte.
- `consistencia_sazonal` só deve ser ligada com evidência de volume por dia
  da semana (use `perfilar_csv.py`); quando ligada, as primeiras semanas
  caem no fallback de média simples e podem gerar falso alerta.
- O histórico de volume do `quality_check` é por data de processamento, não
  do dado: backfill processado num único dia distorce a baseline.
- Origens atrás de CDN/WAF (ex.: Cloudflare) podem recusar IP de nuvem;
  testar da máquina do usuário não prova que a Lambda passa. Avise o
  usuário.
- A origem pode publicar com **defasagem** (o `getConfig` do `ice_ticker`
  informava como último dia um sábado, ~2 dias antes do relógio). Uma
  checagem diária de atualidade que olha "ontem" pode alertar em falso;
  confira no primeiro dia real.
- Parâmetros herdados do DTCC (memória de 1024 MB do `quality_check`, timeout
  do Glue) foram calibrados para outro volume: ao usar o modelo, confronte-os
  com o brief. Com Glue `FLEX`, o timeout **inclui a espera por capacidade**.
- Falhas geram alertas duplicados quando mais de uma regra observa o mesmo
  incidente (Glue falho + state machine falha); decida conscientemente.

## Limites do ambiente (verifique antes de afirmar em docs)

- `terraform` pode estar instalado e ainda assim não rodar `validate`/`plan`:
  o handshake TLS local entre o terraform e os plugins falhou
  (`x509: certificate signed by unknown authority`), provavelmente por
  antivírus/proxy interceptando localhost. `terraform init` funciona. Não
  escreva "sem binário terraform" em `DECISOES.md` sem checar; diga o que
  de fato impediu a validação.
