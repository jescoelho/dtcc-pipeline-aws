"""Lambda que inicia a execução da state machine do pipeline (ver
terraform/step_functions.tf), com nome de execução determinístico
derivado do nome do arquivo .zip -- garante que o mesmo evento de
upload, se entregue duas vezes (S3/EventBridge documentam entrega
"at-least-once", não "exactly-once"), não processe o mesmo arquivo duas
vezes do início ao fim.

Achado real em produção (03/10/2026): o mesmo arquivo
(CFTC_CUMULATIVE_RATES_2026_10_02.csv) gerou DUAS execuções completas e
independentes do pipeline (execution_id diferentes, mesmo RowCount),
cada uma rodando o job Glue do começo ao fim -- não foram concorrentes
(o `MaxConcurrentRuns` padrão do Glue já é 1, teria bloqueado isso se
fossem simultâneas), foram sequenciais: a notificação do S3 chegou
duplicada, cada entrega disparou o fluxo inteiro de novo. Dado não
corrompido (a escrita da Bronze é idempotente, overwrite por partição),
mas processamento e custo desperdiçados, e dois registros concorrendo
no `logs/execucoes/` pra um único arquivo real.

Por que uma Lambda em vez do alvo nativo EventBridge -> Step Functions
direto: o alvo nativo de uma regra do EventBridge não permite definir o
nome da execução a partir do conteúdo do evento -- só controla o input,
nunca o "name" do StartExecution (limitação confirmada: é um pedido de
funcionalidade ainda aberto em ferramentas como CDK/Terraform, não algo
que já existe e eu deixei de configurar). Sem controlar o nome, cada
entrega (duplicada ou não) gera uma execução nova com nome aleatório.

A idempotência real vem da própria API do Step Functions (StartExecution),
não de lógica escrita aqui: numa state machine STANDARD (a que usamos),
chamar StartExecution duas vezes com o MESMO nome e o MESMO input
devolve sucesso idêntico ao da primeira vez, sem criar execução nova;
com o mesmo nome e input diferente, falha com ExecutionAlreadyExists.
Esta Lambda só decide o nome (a partir do nome do arquivo) e trata os
dois casos como sucesso -- já existe uma execução pra esse arquivo, não
importa qual dos dois caminhos a API seguiu internamente.

Gatilho: objeto criado em <zip_prefix>*.zip (ver terraform/step_functions.tf,
regra zip_arrived -- substitui a notificação direta S3 -> unzip_dtcc que
existia antes da state machine).

Variável de ambiente esperada: STATE_MACHINE_ARN.
"""
import json
import os
import re

import boto3

sfn = boto3.client("stepfunctions")

# Nome de execução do Step Functions: até 80 caracteres, sem barra,
# espaço nem outros caracteres especiais (ver documentação oficial da
# API StartExecution) -- um nome de arquivo como
# "raw/dtcc_zip/CFTC_CUMULATIVE_RATES_2026_10_02.zip" tem barra e ponto,
# por isso o saneamento abaixo.
_CARACTERES_INVALIDOS = re.compile(r"[^0-9A-Za-z_-]")


def handler(event, context):
    bucket = event["detail"]["bucket"]["name"]
    key = event["detail"]["object"]["key"]
    nome_arquivo = key.rsplit("/", 1)[-1]
    nome_execucao = _CARACTERES_INVALIDOS.sub("-", nome_arquivo)[:80]

    try:
        resposta = sfn.start_execution(
            stateMachineArn=os.environ["STATE_MACHINE_ARN"],
            name=nome_execucao,
            input=json.dumps(event),
        )
        return {"duplicado": False, "execution_arn": resposta["executionArn"]}
    except sfn.exceptions.ExecutionAlreadyExists:
        # Já existe (ou já existiu) uma execução com este nome -- é
        # exatamente a duplicação que esta Lambda existe pra evitar, não
        # um erro. Ver docstring do módulo.
        return {"duplicado": True, "nome_execucao": nome_execucao}
