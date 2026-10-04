"""Lambda disparada por agendamento (EventBridge Schedule, NÃO reativa a
evento -- ver terraform/atualidade_linhagem.tf) que checa três dimensões
de qualidade que são sobre o *pipeline*, não sobre o dado isolado de um
arquivo (ver README, "Atualidade, consistência sazonal, linhagem de
dado"): por isso não cabem no Glue Data Quality (só vê o arquivo de
hoje) nem nas Lambdas reativas a evento já existentes (quality_check.py,
job_concluido.py) -- as três respondem perguntas sobre a AUSÊNCIA de um
evento, ou sobre um estado que só existe "de fora" do fluxo reativo.

  1. Atualidade: o dia útil anterior a hoje (pulando fim de semana, já
     que o DTCC não publica nesses dias -- ver README,
     03/10/2026) teve pelo menos uma execução (unzip_dtcc rodou)? Hoje
     a ingestão é manual (scripts/ingerir_cumulative.sh -- "Agendar a
     ingestão" é tarefa futura separada, ainda não construída), então
     esta checagem vai alertar em todo dia útil em que ninguém rodar o
     script manualmente. Isso não é um falso positivo: é exatamente o
     que "atualidade" deveria significar, e serve de motivação real pra
     priorizar a próxima tarefa.
  2. Linhagem: toda execução iniciada há mais de MARGEM_LINHAGEM_HORAS
     (tempo de sobra pra Glue e as duas Lambdas em paralelo
     terminarem -- ver terraform/step_functions.tf) tem as 4 etapas
     esperadas na tabela de controle? Uma execução que começa e nunca
     termina (ex.: a Lambda ChecarQualidade falhou silenciosamente, ou
     foi parada manualmente) não aciona nenhum alerta individual -- cada
     etapa, isolada, não "falhou", ela simplesmente nunca aconteceu, e
     só comparar o conjunto de etapas presentes contra o esperado
     enxerga essa lacuna.
  3. Execuções travadas: cruza direto com o Step Functions
     (list_executions, status RUNNING) -- complementar à linhagem, não
     substituto. A tabela de controle diz QUAL etapa falta; o Step
     Functions já sabe, de graça, SE uma execução ainda está rodando,
     sem precisar reconstruir esse estado a partir de registros
     espalhados. Achado da avaliação de eficiência (04/10/2026).

Lê a tabela de controle (logs/execucoes/, ver athena/queries.sql) via
uma query no Athena (tabela controle_execucoes), não mais via
list_objects_v2 + get_object arquivo por arquivo -- antes disso eram
potencialmente centenas de chamadas ao S3 por execução desta Lambda, uma
por registro, pra uma tabela que já existe pronta pra SQL. Troca-se
latência por chamada (a query do Athena é assíncrona: inicia, espera,
busca resultado, alguns segundos) por escalar com o volume de registros,
não com o número de chamadas à API do S3 (achado da avaliação de
eficiência, 04/10/2026). Esta Lambda grava seu próprio veredito como um
registro novo na mesma tabela (origem="checar_pipeline", ainda via
put_object direto -- é 1 escrita por execução desta Lambda, não o padrão
que motivou a troca), e publica no mesmo tópico SNS de observabilidade.tf
quando acha algo.

Variáveis de ambiente esperadas: BUCKET, SNS_TOPIC_ARN, DATABASE,
WORKGROUP, STATE_MACHINE_ARN, JANELA_LINHAGEM_DIAS e
MARGEM_LINHAGEM_HORAS -- as duas últimas vêm do contrato de fonte
(config/fontes/dtcc.yaml), mesmo padrão de
DIAS_HISTORICO/QUEDA_MAXIMA_TOLERADA em quality_check.py. Defaults locais
(3 dias, 2 horas) só pra não quebrar execução fora do Lambda.
"""
import json
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import boto3

s3 = boto3.client("s3")
sns = boto3.client("sns")
athena = boto3.client("athena")
stepfunctions = boto3.client("stepfunctions")

DATABASE = os.environ.get("DATABASE", "")
WORKGROUP = os.environ.get("WORKGROUP", "")
STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")

JANELA_LINHAGEM_DIAS = int(os.environ.get("JANELA_LINHAGEM_DIAS", "3"))
MARGEM_LINHAGEM_HORAS = int(os.environ.get("MARGEM_LINHAGEM_HORAS", "2"))
# Nome da fonte (contrato de fonte, local.fonte.nome) -- mesmo padrão de
# quality_check.py: identifica o remetente no assunto do e-mail de
# alerta. Default "dtcc" preserva o texto de hoje.
NOME_FONTE = os.environ.get("NOME_FONTE", "dtcc")

# As etapas que uma execução completa deve deixar na tabela de
# controle, pelo campo "origem" de cada uma -- mesma lista que
# athena/queries.sql usa na query "fluxo completo de uma execução"
# (ver descompactado_em/disparado_em/qualidade/desfecho_glue ali).
# "trigger_bronze" não entra: foi aposentada em 03/10/2026 (ver
# athena/queries.sql) e não existe mais em execuções novas.
# Vem do contrato de fonte (config/fontes/dtcc.yaml, campo
# etapas_esperadas) -- antes era uma lista fixa aqui, assumindo que toda
# fonte futura teria exatamente estas 4 etapas com estes nomes (achado da
# auditoria de generalização, 04/10/2026). Default local só pra não
# quebrar execução fora do Lambda.
ETAPAS_ESPERADAS = os.environ.get(
    "ETAPAS_ESPERADAS", "unzip_dtcc,glue_data_quality,glue_job,quality_check"
).split(",")


def handler(event, context):
    bucket = os.environ["BUCKET"]
    registros = _ler_tabela_controle(JANELA_LINHAGEM_DIAS)

    problemas = (
        _checar_atualidade(registros)
        + _checar_linhagem(registros)
        + _checar_execucoes_travadas()
    )
    resultado = {"problemas": problemas}
    _registrar_execucao(bucket, resultado)

    if problemas:
        sns.publish(
            TopicArn=os.environ["SNS_TOPIC_ARN"],
            Subject=f"[{NOME_FONTE}-pipeline] atualidade/linhagem: problema encontrado",
            Message="Problemas encontrados:\n- " + "\n- ".join(problemas),
        )

    return resultado


def _consultar_athena(sql: str) -> list:
    """Roda uma query no Athena (start -> poll -> busca resultado,
    mesmo padrão de scripts/configurar_athena.sh) e devolve as linhas
    como lista de dicts coluna->valor -- a API do Athena devolve tudo
    como string (ResultSet.Rows[].Data[].VarCharValue), sem tipagem."""
    query_id = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DATABASE},
        WorkGroup=WORKGROUP,
    )["QueryExecutionId"]

    while True:
        execucao = athena.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]
        estado = execucao["Status"]["State"]
        if estado in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)

    if estado != "SUCCEEDED":
        motivo = execucao["Status"].get("StateChangeReason", "sem motivo informado")
        raise RuntimeError(f"query Athena {query_id} terminou em {estado}: {motivo}")

    colunas = None
    linhas = []
    paginador = athena.get_paginator("get_query_results")
    for pagina in paginador.paginate(QueryExecutionId=query_id):
        for linha in pagina["ResultSet"]["Rows"]:
            valores = [c.get("VarCharValue") for c in linha["Data"]]
            if colunas is None:
                # A primeira linha da primeira página é o header
                # (nome das colunas) -- não se repete nas páginas
                # seguintes.
                colunas = valores
                continue
            linhas.append(dict(zip(colunas, valores)))
    return linhas


def _ler_tabela_controle(dias: int) -> list:
    """Lê os registros de controle_execucoes dos últimos `dias` dias,
    hoje incluso (pra enxergar execuções de hoje que já passaram da
    margem), via Athena -- substitui a leitura direta de
    logs/execucoes/ via list_objects_v2 + get_object (ver docstring do
    módulo). Sem filtrar por origem -- diferente de _media_historica em
    quality_check.py, aqui precisamos de todas as etapas de cada
    execução, não só uma."""
    hoje = datetime.now(timezone.utc).date()
    corte = (hoje - timedelta(days=dias)).isoformat()
    sql = (
        'SELECT "timestamp", origem, execution_id '
        f"FROM {DATABASE}.controle_execucoes "
        f"WHERE dt >= '{corte}'"
    )
    return _consultar_athena(sql)


def _dia_util_anterior(hoje):
    dia = hoje - timedelta(days=1)
    while dia.weekday() >= 5:  # 5 = sábado, 6 = domingo
        dia -= timedelta(days=1)
    return dia


def _checar_atualidade(registros: list) -> list:
    """Só olha o dia útil IMEDIATAMENTE anterior a hoje -- não acumula
    lacunas de dias mais antigos, pra uma falha única não gerar o mesmo
    alerta repetido todo dia depois disso."""
    dia_str = _dia_util_anterior(datetime.now(timezone.utc).date()).isoformat()
    teve_execucao = any(
        r.get("origem") == "unzip_dtcc" and r.get("timestamp", "").startswith(dia_str)
        for r in registros
    )
    if teve_execucao:
        return []
    return [f"nenhuma execução em {dia_str} (dia útil) -- ingestão não rodou"]


def _checar_linhagem(registros: list) -> list:
    """Agrupa por execution_id e confere se as 4 etapas esperadas estão
    todas presentes -- só para execuções iniciadas há mais de
    MARGEM_LINHAGEM_HORAS (senão uma execução ainda em andamento, no
    meio do fluxo, viraria falso alerta)."""
    etapas_por_execucao = defaultdict(set)
    inicio_por_execucao = {}
    for r in registros:
        exec_id = r.get("execution_id")
        if not exec_id:
            continue
        etapas_por_execucao[exec_id].add(r.get("origem"))
        if r.get("origem") == "unzip_dtcc":
            inicio_por_execucao[exec_id] = r.get("timestamp")

    agora = datetime.now(timezone.utc)
    problemas = []
    for exec_id, etapas in etapas_por_execucao.items():
        inicio = inicio_por_execucao.get(exec_id)
        if inicio is None:
            # Sem unzip_dtcc registrado pra esse execution_id -- fora do
            # escopo desta checagem (não dá pra saber quando começou).
            continue
        idade_horas = (agora - datetime.fromisoformat(inicio)).total_seconds() / 3600
        if idade_horas < MARGEM_LINHAGEM_HORAS:
            continue
        faltando = [e for e in ETAPAS_ESPERADAS if e not in etapas]
        if faltando:
            problemas.append(
                f"execução {exec_id} (iniciada há {idade_horas:.1f}h) sem etapa(s): "
                + ", ".join(faltando)
            )
    return problemas


def _checar_execucoes_travadas() -> list:
    """Cruza direto com o Step Functions (list_executions, status
    RUNNING) em vez de só inferir 'travou' pela ausência de etapas na
    tabela de controle -- o Step Functions já sabe, de graça, se uma
    execução está rodando há tempo demais, sem reconstruir esse estado a
    partir de registros espalhados (achado da avaliação de eficiência,
    04/10/2026). Complementar a _checar_linhagem, não substituto: isto
    aqui só vê 'está rodando há tempo demais', sem dizer qual etapa
    especificamente falta -- essa granularidade (por regra de Data
    Quality, inclusive) só a tabela de controle tem.

    Sem STATE_MACHINE_ARN (ex.: execução local de teste sem Lambda),
    pula a checagem em vez de quebrar."""
    if not STATE_MACHINE_ARN:
        return []

    agora = datetime.now(timezone.utc)
    problemas = []
    paginador = stepfunctions.get_paginator("list_executions")
    for pagina in paginador.paginate(stateMachineArn=STATE_MACHINE_ARN, statusFilter="RUNNING"):
        for execucao in pagina.get("executions", []):
            idade_horas = (agora - execucao["startDate"]).total_seconds() / 3600
            if idade_horas < MARGEM_LINHAGEM_HORAS:
                continue
            problemas.append(
                f"execução {execucao['name']} do Step Functions está RUNNING há "
                f"{idade_horas:.1f}h (> {MARGEM_LINHAGEM_HORAS}h) -- possível travamento"
            )
    return problemas


def _registrar_execucao(bucket: str, resultado: dict) -> None:
    agora = datetime.now(timezone.utc)
    registro = {
        "timestamp": agora.isoformat(),
        "origem": "checar_pipeline",
        "status": "problema" if resultado["problemas"] else "ok",
        "problemas": resultado["problemas"],
    }
    log_key = f"logs/execucoes/dt={agora.strftime('%Y-%m-%d')}/{uuid.uuid4()}.json"
    s3.put_object(Bucket=bucket, Key=log_key, Body=json.dumps(registro).encode("utf-8"))
