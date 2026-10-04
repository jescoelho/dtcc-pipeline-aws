"""Testes da Lambda de atualidade/linhagem (lambda/checar_pipeline.py),
com Athena, Step Functions, S3 e SNS mockados -- sem precisar de AWS de
verdade (não dá pra testar isso contra um agendamento real sem esperar
um dia útil passar).

Desde a extensão de eficiência (04/10/2026), a Lambda lê a tabela de
controle via uma query no Athena, não mais via list_objects_v2 +
get_object -- os testes mockam o ciclo start_query_execution ->
get_query_execution -> get_query_results (mesmo padrão assíncrono de
scripts/configurar_athena.sh) em vez de simular conteúdo de
logs/execucoes/dt=<dia>/. Usam as próprias funções de data do módulo
(`_dia_util_anterior`) para não cravar uma data fixa que quebraria
dependendo de quando os testes rodam."""
import importlib
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))

COLUNAS = ["timestamp", "origem", "execution_id"]


def _linha_athena(registro: dict) -> dict:
    """Uma linha do ResultSet do Athena: Data é uma lista de dicts, um
    por coluna, na mesma ordem de COLUNAS -- {} (sem VarCharValue)
    representa NULL, igual a uma célula vazia de verdade."""
    valores = [registro.get(c) for c in COLUNAS]
    return {"Data": [({"VarCharValue": v} if v is not None else {}) for v in valores]}


def _mock_clients(registros: list, execucoes_travadas: list = None):
    """registros: lista plana de dicts {"timestamp", "origem",
    "execution_id"} -- o que a query no Athena devolveria, já sem a
    estrutura por dia que a leitura direta do S3 exigia.
    execucoes_travadas: lista de dicts {"name", "startDate"} que o
    Step Functions devolveria pra list_executions(statusFilter=RUNNING)."""
    execucoes_travadas = execucoes_travadas or []

    fake_athena = mock.Mock()
    fake_athena.start_query_execution.return_value = {"QueryExecutionId": "qid-1"}
    fake_athena.get_query_execution.return_value = {
        "QueryExecution": {"Status": {"State": "SUCCEEDED"}}
    }
    linhas = [{"Data": [{"VarCharValue": c} for c in COLUNAS]}]  # header
    linhas += [_linha_athena(r) for r in registros]
    fake_paginador_athena = mock.Mock()
    fake_paginador_athena.paginate.return_value = [{"ResultSet": {"Rows": linhas}}]
    fake_athena.get_paginator.return_value = fake_paginador_athena

    fake_sfn = mock.Mock()
    fake_paginador_sfn = mock.Mock()
    fake_paginador_sfn.paginate.return_value = [{"executions": execucoes_travadas}]
    fake_sfn.get_paginator.return_value = fake_paginador_sfn

    fake_s3 = mock.Mock()
    fake_sns = mock.Mock()

    def _client(nome, *a, **kw):
        return {
            "s3": fake_s3,
            "sns": fake_sns,
            "athena": fake_athena,
            "stepfunctions": fake_sfn,
        }[nome]

    return fake_s3, fake_sns, fake_athena, fake_sfn, _client


def _carregar_modulo(
    monkeypatch, client_fn, janela_dias="3", margem_horas="2", state_machine_arn=""
):
    monkeypatch.setenv("BUCKET", "meu-bucket")
    monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123:jessica-dtcclab-alertas")
    monkeypatch.setenv("DATABASE", "meu_banco")
    monkeypatch.setenv("WORKGROUP", "meu-workgroup")
    monkeypatch.setenv("STATE_MACHINE_ARN", state_machine_arn)
    monkeypatch.setenv("JANELA_LINHAGEM_DIAS", janela_dias)
    monkeypatch.setenv("MARGEM_LINHAGEM_HORAS", margem_horas)
    with mock.patch("boto3.client", side_effect=client_fn):
        import checar_pipeline

        importlib.reload(checar_pipeline)
        return checar_pipeline


def test_atualidade_sem_execucao_no_dia_util_anterior_publica_alerta(monkeypatch):
    _, fake_sns, *_r, client_fn = _mock_clients([])  # tabela de controle vazia
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler({}, context=None)

    assert any("ingestão não rodou" in p for p in resposta["problemas"])
    fake_sns.publish.assert_called_once()


def test_atualidade_com_execucao_no_dia_util_anterior_nao_alerta(monkeypatch):
    _, _, _, _, client_fn_vazio = _mock_clients([])
    modulo = _carregar_modulo(monkeypatch, client_fn_vazio)
    dia_anterior = modulo._dia_util_anterior(datetime.now(timezone.utc).date())

    registro = {
        "origem": "unzip_dtcc",
        "timestamp": f"{dia_anterior.isoformat()}T12:00:00+00:00",
        "execution_id": "exec-1",
    }
    _, fake_sns, _, _, client_fn = _mock_clients([registro])
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler({}, context=None)

    assert not any("ingestão não rodou" in p for p in resposta["problemas"])


def test_linhagem_execucao_completa_e_antiga_nao_alerta(monkeypatch):
    agora = datetime.now(timezone.utc)
    inicio = (agora - timedelta(hours=5)).isoformat()
    registros = [
        {"origem": "unzip_dtcc", "timestamp": inicio, "execution_id": "exec-ok"},
        {"origem": "glue_data_quality", "execution_id": "exec-ok"},
        {"origem": "glue_job", "execution_id": "exec-ok"},
        {"origem": "quality_check", "execution_id": "exec-ok"},
    ]
    _, _, _, _, client_fn = _mock_clients(registros)
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler({}, context=None)

    assert not any("exec-ok" in p for p in resposta["problemas"])


def test_linhagem_execucao_incompleta_e_antiga_alerta(monkeypatch):
    agora = datetime.now(timezone.utc)
    inicio = (agora - timedelta(hours=5)).isoformat()
    # Só descompactou e o Glue Data Quality rodou -- faltam glue_job e
    # quality_check, e já passou bem da margem de 2h.
    registros = [
        {"origem": "unzip_dtcc", "timestamp": inicio, "execution_id": "exec-incompleta"},
        {"origem": "glue_data_quality", "execution_id": "exec-incompleta"},
    ]
    _, fake_sns, _, _, client_fn = _mock_clients(registros)
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler({}, context=None)

    problema = next(p for p in resposta["problemas"] if "exec-incompleta" in p)
    assert "glue_job" in problema
    assert "quality_check" in problema
    fake_sns.publish.assert_called_once()


def test_linhagem_execucao_incompleta_mas_recente_nao_alerta(monkeypatch):
    agora = datetime.now(timezone.utc)
    inicio = (agora - timedelta(minutes=10)).isoformat()
    # Só descompactou até agora -- mas começou há 10 minutos, dentro da
    # margem de 2h, então ainda pode estar em andamento (não é lacuna).
    registros = [
        {"origem": "unzip_dtcc", "timestamp": inicio, "execution_id": "exec-em-andamento"},
    ]
    _, _, _, _, client_fn = _mock_clients(registros)
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler({}, context=None)

    assert not any("exec-em-andamento" in p for p in resposta["problemas"])


def test_registros_sem_execution_id_nao_quebram_a_checagem(monkeypatch):
    # Registros antigos (ex.: trigger_bronze aposentado) podem não ter
    # execution_id -- não devem ser agrupados nem quebrar a linhagem.
    registros = [{"origem": "trigger_bronze", "execution_id": None}]
    _, _, _, _, client_fn = _mock_clients(registros)
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler({}, context=None)

    # Não deve haver nenhum problema de linhagem (só, possivelmente, o
    # de atualidade, que é checado separadamente) por causa do registro
    # sem execution_id.
    assert not any("sem etapa" in p for p in resposta["problemas"])


def test_registra_propria_execucao_na_tabela_de_controle(monkeypatch):
    fake_s3, fake_sns, _, _, client_fn = _mock_clients([])
    modulo = _carregar_modulo(monkeypatch, client_fn)

    modulo.handler({}, context=None)

    fake_s3.put_object.assert_called_once()
    kwargs = fake_s3.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "meu-bucket"
    assert kwargs["Key"].startswith("logs/execucoes/dt=")
    registro = json.loads(kwargs["Body"])
    assert registro["origem"] == "checar_pipeline"
    assert registro["status"] == "problema"  # tabela vazia -> atualidade falha


def test_execucao_running_ha_mais_que_margem_alerta(monkeypatch):
    agora = datetime.now(timezone.utc)
    execucoes = [{"name": "exec-travada", "startDate": agora - timedelta(hours=5)}]
    _, fake_sns, _, _, client_fn = _mock_clients([], execucoes_travadas=execucoes)
    modulo = _carregar_modulo(
        monkeypatch, client_fn, state_machine_arn="arn:aws:states:us-east-1:123:stateMachine:pipeline"
    )

    resposta = modulo.handler({}, context=None)

    problema = next(p for p in resposta["problemas"] if "exec-travada" in p)
    assert "RUNNING" in problema
    fake_sns.publish.assert_called_once()


def test_execucao_running_dentro_da_margem_nao_alerta(monkeypatch):
    agora = datetime.now(timezone.utc)
    execucoes = [{"name": "exec-recente", "startDate": agora - timedelta(minutes=10)}]
    _, _, _, _, client_fn = _mock_clients([], execucoes_travadas=execucoes)
    modulo = _carregar_modulo(
        monkeypatch, client_fn, state_machine_arn="arn:aws:states:us-east-1:123:stateMachine:pipeline"
    )

    resposta = modulo.handler({}, context=None)

    assert not any("exec-recente" in p for p in resposta["problemas"])


def test_sem_state_machine_arn_pula_checagem_de_travamento(monkeypatch):
    # STATE_MACHINE_ARN vazio (default do _carregar_modulo) -- não deve
    # chamar o Step Functions nem quebrar.
    fake_s3, fake_sns, fake_athena, fake_sfn, client_fn = _mock_clients([])
    modulo = _carregar_modulo(monkeypatch, client_fn)

    modulo.handler({}, context=None)

    fake_sfn.get_paginator.assert_not_called()
