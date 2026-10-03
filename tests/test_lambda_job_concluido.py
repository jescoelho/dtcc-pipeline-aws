"""Testes da Lambda que registra o desfecho do Glue Bronze
(lambda/job_concluido.py), com Glue e S3 mockados -- sem precisar de AWS
de verdade.

Evento no formato nativo "Glue Job State Change" do EventBridge (não o
"S3 Object Created" que as outras Lambdas deste pipeline recebem)."""
import importlib
import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))


def _evento_glue(job_name: str, job_run_id: str, state: str) -> dict:
    return {
        "source": "aws.glue",
        "detail-type": "Glue Job State Change",
        "detail": {"jobName": job_name, "jobRunId": job_run_id, "state": state},
    }


def _mock_clients(job_run_attrs: dict):
    fake_glue = mock.Mock()
    fake_glue.get_job_run.return_value = {"JobRun": job_run_attrs}
    fake_s3 = mock.Mock()

    def _client(nome, *a, **kw):
        return {"glue": fake_glue, "s3": fake_s3}[nome]

    return fake_glue, fake_s3, _client


def _carregar_modulo(monkeypatch, client_fn):
    monkeypatch.setenv("BUCKET", "meu-bucket")
    with mock.patch("boto3.client", side_effect=client_fn):
        import job_concluido

        importlib.reload(job_concluido)
        return job_concluido


def test_sucesso_grava_registro_com_duracao_e_execution_id(monkeypatch):
    fake_glue, fake_s3, client_fn = _mock_clients(
        {"Arguments": {"--execution_id": "exec-abc", "--JOB_NAME": "x"}, "ExecutionTime": 42}
    )
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler(_evento_glue("job-x", "jr_1", "SUCCEEDED"), context=None)

    fake_glue.get_job_run.assert_called_once_with(JobName="job-x", RunId="jr_1")
    assert resposta["concluido"]["state"] == "SUCCEEDED"
    assert resposta["concluido"]["execution_id"] == "exec-abc"
    assert resposta["concluido"]["duracao_segundos"] == 42

    kwargs = fake_s3.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "meu-bucket"
    assert kwargs["Key"].startswith("logs/execucoes/dt=")
    registro = json.loads(kwargs["Body"])
    assert registro["origem"] == "glue_job"
    assert registro["job_run_id"] == "jr_1"
    assert registro["execution_id"] == "exec-abc"
    assert registro["status"] == "succeeded"
    assert registro["duracao_segundos"] == 42


def test_falha_grava_status_minusculo_igual_ao_sucesso(monkeypatch):
    fake_glue, fake_s3, client_fn = _mock_clients(
        {"Arguments": {"--execution_id": "exec-xyz"}, "ExecutionTime": 7}
    )
    modulo = _carregar_modulo(monkeypatch, client_fn)

    modulo.handler(_evento_glue("job-x", "jr_2", "FAILED"), context=None)

    kwargs = fake_s3.put_object.call_args.kwargs
    registro = json.loads(kwargs["Body"])
    assert registro["status"] == "failed"


def test_sem_execution_id_no_run_nao_quebra(monkeypatch):
    fake_glue, fake_s3, client_fn = _mock_clients({"Arguments": {}, "ExecutionTime": 3})
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler(_evento_glue("job-x", "jr_3", "TIMEOUT"), context=None)

    assert resposta["concluido"]["execution_id"] is None
