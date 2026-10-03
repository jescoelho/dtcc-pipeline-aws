"""Testes da Lambda que dispara o Glue Bronze (lambda/trigger_bronze.py),
com o client do Glue mockado -- sem precisar de AWS de verdade.

Evento no formato "S3 Object Created" do EventBridge (não o {"Records":
[...]} do S3 direto) -- ver o docstring de lambda/trigger_bronze.py."""
import importlib
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))


def _evento_eventbridge(bucket: str, key: str) -> dict:
    return {
        "detail-type": "Object Created",
        "source": "aws.s3",
        "detail": {"bucket": {"name": bucket}, "object": {"key": key}},
    }


def test_dispara_start_job_run_com_o_job_certo(monkeypatch):
    monkeypatch.setenv("GLUE_JOB_NAME", "jessica-dtcclab-bronze-ingest")

    with mock.patch("boto3.client") as mock_client:
        fake_glue = mock.Mock()
        fake_glue.start_job_run.return_value = {"JobRunId": "jr_123"}
        mock_client.return_value = fake_glue

        import trigger_bronze
        importlib.reload(trigger_bronze)

        resposta = trigger_bronze.handler(
            _evento_eventbridge(
                "bucket-teste", "raw/dtcc/CFTC_CUMULATIVE_RATES_2026_10_02.csv"
            ),
            context=None,
        )

        fake_glue.start_job_run.assert_called_once_with(JobName="jessica-dtcclab-bronze-ingest")
        assert resposta["disparado"]["job_run_id"] == "jr_123"
        assert (
            resposta["disparado"]["csv"]
            == "s3://bucket-teste/raw/dtcc/CFTC_CUMULATIVE_RATES_2026_10_02.csv"
        )


def test_cada_evento_dispara_uma_execucao_independente(monkeypatch):
    monkeypatch.setenv("GLUE_JOB_NAME", "job-x")

    with mock.patch("boto3.client") as mock_client:
        fake_glue = mock.Mock()
        fake_glue.start_job_run.side_effect = [{"JobRunId": "jr_1"}, {"JobRunId": "jr_2"}]
        mock_client.return_value = fake_glue

        import trigger_bronze
        importlib.reload(trigger_bronze)

        resposta_1 = trigger_bronze.handler(_evento_eventbridge("b", "raw/dtcc/a.csv"), context=None)
        resposta_2 = trigger_bronze.handler(_evento_eventbridge("b", "raw/dtcc/b.csv"), context=None)

        assert resposta_1["disparado"]["job_run_id"] == "jr_1"
        assert resposta_2["disparado"]["job_run_id"] == "jr_2"
        assert fake_glue.start_job_run.call_count == 2
