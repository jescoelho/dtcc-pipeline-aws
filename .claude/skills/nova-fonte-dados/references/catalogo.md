# Catálogo — o que a skill já sabe fazer

Leia antes do passo 1: **reutilize antes de escrever à mão.** Atualizado no
passo 10 (ver `references/evolucao.md`, que define os critérios e o
registro de mudanças).

## Ferramentas (`scripts/`)

| Ferramenta | Use quando | Entrada → saída | Origem |
|---|---|---|---|
| `sondar_origem.py <url>` | Primeiro contato com **qualquer** URL (passo 1) | URL → `robots.txt`, tipo (arquivo/listagem/API/SPA), endpoints candidatos lidos dos scripts da página, links e menções de termos de uso. Só GETs estáticos; nunca pede token nem chama endpoint de dados | `ice_ticker` |
| `perfilar_csv.py <arquivos…>` | Já há ≥1 amostra CSV (passos 1 e 5); melhor com vários dias | CSVs → formato, estabilidade do header, preenchimento/distintos por coluna, candidatas a `coluna_id` (único entre todos os arquivos) e a domínio, colunas ruins p/ Parquet, volume por dia da semana e evidência para `consistencia_sazonal` | `ice_ticker` |
| `baixar_listagem.py` | A origem é listagem de diretório HTTP (subpasta/ano/mês) | Base + template + regex → arquivos baixados, idempotente | anterior |
| `validar_terraform.py` | Passo 8 | `.tf` + contrato → balanceamento e referências | anterior |
| `smoke_test.py` | Passo 10, após alterar scripts | — → ok/falha de `--help` e de testes sintéticos de `perfilar_csv`/`validar_terraform` | avaliação |

## Exemplos concluídos (pipelines completos em `.claude/outputs/`)

| Pipeline | Mecanismo de ingestão | O que tem de interessante para reuso |
|---|---|---|
| `ice_ticker` | API HTTP com token anônimo (SPA); CSV direto, sem `.zip`; janela de 1 ano | `lambda/ingerir_ice_ticker.py` (token → config → export, validação do header, alerta SNS); `unzip_dtcc.py` que aceita `.csv`; testes de HTTP com `urlopen` mockado; `ingestao_http.tf` |
| DTCC (repositório principal, modelo) | Bucket S3 público, `CopyObject` | Referência de todo o resto |

## Candidatos (vistos 1 vez; extrair na 2ª ocorrência — regra de duas)

- **Lambda de ingestão HTTP**: esqueleto comum = autenticar (se preciso),
  descobrir a data, baixar com timeout, validar que é o formato esperado,
  gravar em `zip_prefix`, registrar na tabela de controle, alertar por SNS.
  Extrair como template quando uma 2ª origem HTTP aparecer.
- **Teste padrão de ingestão HTTP** (feliz, data fora da janela, resposta 200
  que não é o formato, 403, data mal formada).

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
