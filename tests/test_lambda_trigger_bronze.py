"""Testes da Lambda que dispara o Glue Bronze (lambda/trigger_bronze.py),
com o client do Glue mockado -- sem precisar de AWS de verdade."""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))


def _evento_s3(bucket: str, key: str) -> dict:
    return {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}


def test_dispara_start_job_run_com_o_job_certo(monkeypatch):
    monkeypatch.setenv("GLUE_JOB_NAME", "jessica-dtcclab-bronze-ingest")

    with mock.patch("boto3.client") as mock_client:
        fake_glue = mock.Mock()
        fake_glue.start_job_run.return_value = {"JobRunId": "jr_123"}
        mock_client.return_value = fake_glue

        import trigger_bronze
        import importlib
        importlib.reload(trigger_bronze)

        resposta = trigger_bronze.handler(
            _evento_s3("bucket-teste", "raw/dtcc/CFTC_CUMULATIVE_RATES_2026_10_02.csv"),
            context=None,
        )

        fake_glue.start_job_run.assert_called_once_with(JobName="jessica-dtcclab-bronze-ingest")
        assert resposta["disparados"][0]["job_run_id"] == "jr_123"
        assert resposta["disparados"][0]["csv"] == "s3://bucket-teste/raw/dtcc/CFTC_CUMULATIVE_RATES_2026_10_02.csv"


def test_processa_varios_records_do_mesmo_evento(monkeypatch):
    monkeypatch.setenv("GLUE_JOB_NAME", "job-x")

    with mock.patch("boto3.client") as mock_client:
        fake_glue = mock.Mock()
        fake_glue.start_job_run.side_effect = [{"JobRunId": "jr_1"}, {"JobRunId": "jr_2"}]
        mock_client.return_value = fake_glue

        import trigger_bronze
        import importlib
        importlib.reload(trigger_bronze)

        evento = {
            "Records": [
                {"s3": {"bucket": {"name": "b"}, "object": {"key": "raw/dtcc/a.csv"}}},
                {"s3": {"bucket": {"name": "b"}, "object": {"key": "raw/dtcc/b.csv"}}},
            ]
        }
        resposta = trigger_bronze.handler(evento, context=None)

        assert len(resposta["disparados"]) == 2
        assert fake_glue.start_job_run.call_count == 2
