"""Testes da Lambda de atualidade/linhagem (lambda/checar_pipeline.py),
com S3 e SNS mockados -- sem precisar de AWS de verdade (não dá pra
testar isso contra um agendamento real sem esperar um dia útil passar).

Diferente dos testes de quality_check.py (um evento por teste), esta
Lambda lê a tabela de controle inteira de uma vez -- os testes montam o
conteúdo de `logs/execucoes/dt=<dia>/` para os dias que
`_ler_tabela_controle` vai consultar, e usam as próprias funções de data
do módulo (`_dia_util_anterior`) para não cravar uma data fixa que
quebraria dependendo de quando os testes rodam."""
import importlib
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))


def _mock_clients(registros_por_dia: dict):
    """registros_por_dia: {"2026-10-02": [registro, registro, ...]} --
    um "arquivo" por registro em logs/execucoes/dt=<dia>/<uuid>.json,
    igual ao que cada etapa real grava."""
    corpo_por_key = {}
    contents_por_prefixo = {}
    for dia, registros in registros_por_dia.items():
        prefixo = f"logs/execucoes/dt={dia}/"
        contents = []
        for registro in registros:
            key = f"{prefixo}{uuid.uuid4()}.json"
            corpo_por_key[key] = json.dumps(registro).encode("utf-8")
            contents.append({"Key": key})
        contents_por_prefixo[prefixo] = contents

    fake_s3 = mock.Mock()
    fake_s3.get_object.side_effect = lambda Bucket, Key: {
        "Body": __import__("io").BytesIO(corpo_por_key[Key])
    }

    fake_paginator = mock.Mock()

    def _paginate(Bucket, Prefix):
        return [{"Contents": contents_por_prefixo.get(Prefix, [])}]

    fake_paginator.paginate.side_effect = _paginate
    fake_s3.get_paginator.return_value = fake_paginator

    fake_sns = mock.Mock()

    def _client(nome, *a, **kw):
        return {"s3": fake_s3, "sns": fake_sns}[nome]

    return fake_s3, fake_sns, _client


def _carregar_modulo(monkeypatch, client_fn, janela_dias="3", margem_horas="2"):
    monkeypatch.setenv("BUCKET", "meu-bucket")
    monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123:jessica-dtcclab-alertas")
    monkeypatch.setenv("JANELA_LINHAGEM_DIAS", janela_dias)
    monkeypatch.setenv("MARGEM_LINHAGEM_HORAS", margem_horas)
    with mock.patch("boto3.client", side_effect=client_fn):
        import checar_pipeline

        importlib.reload(checar_pipeline)
        return checar_pipeline


def test_atualidade_sem_execucao_no_dia_util_anterior_publica_alerta(monkeypatch):
    fake_s3, fake_sns, client_fn = _mock_clients({})  # tabela de controle vazia
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler({}, context=None)

    assert any("ingestão não rodou" in p for p in resposta["problemas"])
    fake_sns.publish.assert_called_once()


def test_atualidade_com_execucao_no_dia_util_anterior_nao_alerta(monkeypatch):
    # Carrega o módulo uma vez só pra usar _dia_util_anterior (a mesma
    # lógica de data que o handler vai usar), e de novo depois de montar
    # o registro certo pra essa data.
    _, _, client_fn_vazio = _mock_clients({})
    modulo = _carregar_modulo(monkeypatch, client_fn_vazio)
    dia_anterior = modulo._dia_util_anterior(datetime.now(timezone.utc).date())

    registro = {
        "origem": "unzip_dtcc",
        "timestamp": f"{dia_anterior.isoformat()}T12:00:00+00:00",
        "execution_id": "exec-1",
        "status": "descompactado",
    }
    fake_s3, fake_sns, client_fn = _mock_clients({dia_anterior.isoformat(): [registro]})
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler({}, context=None)

    assert not any("ingestão não rodou" in p for p in resposta["problemas"])


def test_linhagem_execucao_completa_e_antiga_nao_alerta(monkeypatch):
    agora = datetime.now(timezone.utc)
    inicio = (agora - timedelta(hours=5)).isoformat()
    hoje = agora.date().isoformat()
    registros = [
        {"origem": "unzip_dtcc", "timestamp": inicio, "execution_id": "exec-ok"},
        {"origem": "glue_data_quality", "execution_id": "exec-ok"},
        {"origem": "glue_job", "execution_id": "exec-ok", "status": "succeeded"},
        {"origem": "quality_check", "execution_id": "exec-ok", "linhas": 100},
    ]
    fake_s3, fake_sns, client_fn = _mock_clients({hoje: registros})
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler({}, context=None)

    assert not any("exec-ok" in p for p in resposta["problemas"])


def test_linhagem_execucao_incompleta_e_antiga_alerta(monkeypatch):
    agora = datetime.now(timezone.utc)
    inicio = (agora - timedelta(hours=5)).isoformat()
    hoje = agora.date().isoformat()
    # Só descompactou e o Glue Data Quality rodou -- faltam glue_job e
    # quality_check, e já passou bem da margem de 2h.
    registros = [
        {"origem": "unzip_dtcc", "timestamp": inicio, "execution_id": "exec-incompleta"},
        {"origem": "glue_data_quality", "execution_id": "exec-incompleta"},
    ]
    fake_s3, fake_sns, client_fn = _mock_clients({hoje: registros})
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler({}, context=None)

    problema = next(p for p in resposta["problemas"] if "exec-incompleta" in p)
    assert "glue_job" in problema
    assert "quality_check" in problema
    fake_sns.publish.assert_called_once()


def test_linhagem_execucao_incompleta_mas_recente_nao_alerta(monkeypatch):
    agora = datetime.now(timezone.utc)
    inicio = (agora - timedelta(minutes=10)).isoformat()
    hoje = agora.date().isoformat()
    # Só descompactou até agora -- mas começou há 10 minutos, dentro da
    # margem de 2h, então ainda pode estar em andamento (não é lacuna).
    registros = [
        {"origem": "unzip_dtcc", "timestamp": inicio, "execution_id": "exec-em-andamento"},
    ]
    fake_s3, fake_sns, client_fn = _mock_clients({hoje: registros})
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler({}, context=None)

    assert not any("exec-em-andamento" in p for p in resposta["problemas"])


def test_registros_sem_execution_id_nao_quebram_a_checagem(monkeypatch):
    # Registros antigos (ex.: trigger_bronze aposentado) podem não ter
    # execution_id -- não devem ser agrupados nem quebrar a linhagem.
    hoje = datetime.now(timezone.utc).date().isoformat()
    registros = [{"origem": "trigger_bronze", "job_run_id": "jr_1"}]
    fake_s3, fake_sns, client_fn = _mock_clients({hoje: registros})
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler({}, context=None)

    # Não deve haver nenhum problema de linhagem (só, possivelmente, o
    # de atualidade, que é checado separadamente) por causa do registro
    # sem execution_id.
    assert not any("sem etapa" in p for p in resposta["problemas"])


def test_registra_propria_execucao_na_tabela_de_controle(monkeypatch):
    fake_s3, fake_sns, client_fn = _mock_clients({})
    modulo = _carregar_modulo(monkeypatch, client_fn)

    modulo.handler({}, context=None)

    fake_s3.put_object.assert_called_once()
    kwargs = fake_s3.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "meu-bucket"
    assert kwargs["Key"].startswith("logs/execucoes/dt=")
    registro = json.loads(kwargs["Body"])
    assert registro["origem"] == "checar_pipeline"
    assert registro["status"] == "problema"  # tabela vazia -> atualidade falha
