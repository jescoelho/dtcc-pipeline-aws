# Mecanismo de ingestão — decidir, não assumir

Leia este arquivo no passo 3 do procedimento (ver `SKILL.md`), depois de já
ter investigado a URL e os dados reais no passo 1. Qual seção se aplica
depende inteiramente do que a investigação revelou sobre a origem — escolha
uma, não tente generalizar as três num código só.

Se `pular_ingestao=true` foi passado como parâmetro de execução, nenhuma
seção abaixo se aplica: documente essa opção no YAML/`DECISOES_MD` e siga
para o passo 4 — o usuário vai alimentar a fonte manualmente por enquanto.

O padrão hoje (a Lambda de ingestão lida na pré-leitura, ex.
`ingerir_cumulative.py`) assume uma origem que é um bucket S3 público com
nome de arquivo previsível, copiado via `CopyObject` (cópia
servidor-a-servidor, nunca passa pela Lambda). Isso só se aplica quando a
origem real é de fato um bucket S3 — nunca force esse caso se a investigação
mostrou outra coisa.

## Caso 1 — outro bucket S3 público, nome de arquivo previsível por data

Reaproveite o padrão exato da Lambda de referência — crie
`LAMBDA_DIR/ingerir_<nome>.py` como uma cópia adaptada:

- Variáveis de ambiente equivalentes: bucket de origem, padrão de nome do
  arquivo de origem, prefixo de destino no bucket do laboratório, nome do
  bucket do laboratório, ARN do tópico SNS compartilhado, nome da fonte —
  mais o que for específico desta fonte (ex. parâmetros extras que entram no
  padrão de nome).
- Mesma estrutura de agendamento (EventBridge Schedule) e mesmo padrão de
  alerta (publica no tópico SNS compartilhado em caso de falha).

## Caso 2 — link HTTP direto (não S3), arquivo estático por data ou fixo

Escreva uma Lambda nova que baixa via HTTP (`urllib3`/`requests`, já
disponível no runtime do Lambda ou embutido no pacote da função) e grava no
bucket do laboratório via `s3.put_object`:

- Mesma estrutura de agendamento e mesmo padrão de alerta do Caso 1 — não
  existe evento nativo da AWS para "download HTTP falhou", então o alerta é
  publicado direto do código da própria Lambda, do mesmo jeito que a Lambda
  de referência já faz pra "a cópia entre buckets falhou".
- Trate timeout e erro HTTP (404, 403, 5xx) como falha a alertar, não como
  exceção não tratada — o objetivo é que o alerta chegue ao SNS mesmo
  quando a origem está fora do ar ou mudou de formato.

## Recurso — origem com listagem de diretório (`scripts/baixar_listagem.py`)

Quando a investigação do passo 1 mostrar que a origem é uma listagem de
diretório HTTP (subpasta/ano/mês com arquivos por data), use
`scripts/baixar_listagem.py` para obter a amostra real e fazer backfill.
Ferramenta geral e parametrizável (`--help` lista tudo): base, template
de caminho, regex de arquivo, período, destino, headers. Idempotente;
espelha o caminho relativo da origem na pasta de saída.

Exemplo (origem com `<subpasta>/<AAAA>/<MM>/ARQ.<AAAAMMDD>.csv.zip`):

    python scripts/baixar_listagem.py --base-url <URL base> --list-subdirs
    python scripts/baixar_listagem.py --base-url <URL base> \
        --subdirs <subpasta> --start AAAA-MM --end AAAA-MM --out <pasta> \
        --file-regex '<regex do arquivo diário>' --dry-run   # tire --dry-run p/ baixar

Regras ao usar:

- Antes de rodar, verifique os termos de uso e o `robots.txt` da origem.
  A ferramenta consulta o `robots.txt`, usa User-Agent próprio e para em
  401/403; **não** a altere nem passe `--user-agent`/`--header`/`--ignore-robots`
  para disfarçar o acesso a uma origem que o recusa.
- A skill não executa downloads em massa por conta própria: sugira o
  comando e peça ao usuário o caminho dos arquivos baixados, ou rode só
  `--list-subdirs`/`--dry-run`/um intervalo mínimo quando o acesso for
  claramente permitido. Infira o contrato a partir dos arquivos reais.
- Se a origem recusar o acesso, a ingestão agendada tampouco funcionará
  (Lambda sai de IP de nuvem): use `pular_ingestao=true` (usuário sobe os
  arquivos ao `zip_prefix`) e registre no contrato e em `DECISOES_MD` que
  a automação depende de autorização da origem.
- O padrão de nome vindo da documentação ou de exemplos não está
  verificado até haver um arquivo real; confirme no passo 1.

## Caso 3 — API paginada/autenticada, ou link do dia só descoberto numa página índice

Ainda assim, uma Lambda agendada que faz a requisição necessária e grava o
resultado no bucket — mas documente explicitamente a complexidade adicional
no cabeçalho do arquivo e em `DECISOES_MD`, do mesmo jeito que o código
existente documenta cada decisão não trivial:

- Paginação: até onde a Lambda pagina por execução, e como ela sabe que
  terminou.
- Autenticação: onde a credencial fica armazenada (nunca cravada no código
  — Secrets Manager ou variável de ambiente, dependendo do que o usuário já
  usa no restante da conta) e o que acontece se ela expirar.
- Descoberta de link: a lógica que acha "o link de hoje" numa página índice
  é, ela mesma, uma dependência frágil (a página pode mudar de layout) — vale
  a pena logar o link encontrado antes de usá-lo, pra facilitar diagnosticar
  se a Lambda um dia pegar o link errado.
