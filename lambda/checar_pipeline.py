"""Lambda disparada por agendamento (EventBridge Schedule, NÃO reativa a
evento -- ver terraform/atualidade_linhagem.tf) que checa duas dimensões
de qualidade que são sobre o *pipeline*, não sobre o dado isolado de um
arquivo (ver README, "Atualidade, consistência sazonal, linhagem de
dado"): por isso não cabem no Glue Data Quality (só vê o arquivo de
hoje) nem nas Lambdas reativas a evento já existentes (quality_check.py,
job_concluido.py) -- as duas respondem perguntas sobre a AUSÊNCIA de um
evento, e um runner reativo nunca é acionado por algo que não aconteceu.

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

Lê a tabela de controle inteira (logs/execucoes/, ver
athena/queries.sql) pela primeira vez -- as demais etapas só escrevem o
próprio registro, ou (quality_check.py, _media_historica) leem uma
janela estreita filtrada por origem. Esta Lambda grava seu próprio
veredito como um registro novo na mesma tabela (origem="checar_pipeline"),
e publica no mesmo tópico SNS de observabilidade.tf quando acha algo.

Variáveis de ambiente esperadas: BUCKET, SNS_TOPIC_ARN,
JANELA_LINHAGEM_DIAS e MARGEM_LINHAGEM_HORAS -- as duas últimas vêm do
contrato de fonte (config/fontes/dtcc.yaml), mesmo padrão de
DIAS_HISTORICO/QUEDA_MAXIMA_TOLERADA em quality_check.py. Defaults locais
(3 dias, 2 horas) só pra não quebrar execução fora do Lambda.
"""
import json
import os
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import boto3

s3 = boto3.client("s3")
sns = boto3.client("sns")

JANELA_LINHAGEM_DIAS = int(os.environ.get("JANELA_LINHAGEM_DIAS", "3"))
MARGEM_LINHAGEM_HORAS = int(os.environ.get("MARGEM_LINHAGEM_HORAS", "2"))

# As 4 etapas que uma execução completa deve deixar na tabela de
# controle, pelo campo "origem" de cada uma -- mesma lista que
# athena/queries.sql usa na query "fluxo completo de uma execução"
# (ver descompactado_em/disparado_em/qualidade/desfecho_glue ali).
# "trigger_bronze" não entra: foi aposentada em 03/10/2026 (ver
# athena/queries.sql) e não existe mais em execuções novas.
ETAPAS_ESPERADAS = ["unzip_dtcc", "glue_data_quality", "glue_job", "quality_check"]


def handler(event, context):
    bucket = os.environ["BUCKET"]
    registros = _ler_tabela_controle(bucket, JANELA_LINHAGEM_DIAS)

    problemas = _checar_atualidade(registros) + _checar_linhagem(registros)
    resultado = {"problemas": problemas}
    _registrar_execucao(bucket, resultado)

    if problemas:
        sns.publish(
            TopicArn=os.environ["SNS_TOPIC_ARN"],
            Subject="[dtcc-pipeline] atualidade/linhagem: problema encontrado",
            Message="Problemas encontrados:\n- " + "\n- ".join(problemas),
        )

    return resultado


def _ler_tabela_controle(bucket: str, dias: int) -> list:
    """Lê todos os registros de logs/execucoes/ dos últimos `dias` dias,
    hoje incluso (pra enxergar execuções de hoje que já passaram da
    margem). Sem filtrar por origem -- diferente de _media_historica em
    quality_check.py, aqui precisamos de todas as etapas de cada
    execução, não só uma."""
    hoje = datetime.now(timezone.utc).date()
    paginador = s3.get_paginator("list_objects_v2")
    registros = []
    for i in range(dias + 1):
        dia = hoje - timedelta(days=i)
        prefixo = f"logs/execucoes/dt={dia.isoformat()}/"
        for pagina in paginador.paginate(Bucket=bucket, Prefix=prefixo):
            for item in pagina.get("Contents", []):
                corpo = s3.get_object(Bucket=bucket, Key=item["Key"])["Body"].read()
                registros.append(json.loads(corpo))
    return registros


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
