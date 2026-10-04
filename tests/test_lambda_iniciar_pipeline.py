"""Testes da Lambda que inicia a state machine (lambda/iniciar_pipeline.py),
com o cliente do Step Functions mockado -- sem precisar de AWS de
verdade. Foco: o nome de execução sai saneado do nome do arquivo, e os
dois desfechos da API (nova execução vs. ExecutionAlreadyExists) são
tratados como sucesso, não erro -- é exatamente a proteção contra
entrega duplicada que esta Lambda existe pra fazer (ver docstring do
módulo)."""
import importlib
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))


class _ExecutionAlreadyExists(Exception):
    pass


def _evento(bucket: str, key: str) -> dict:
    return {
        "detail-type": "Object Created",
        "source": "aws.s3",
        "detail": {"bucket": {"name": bucket}, "object": {"key": key}},
    }


def _carregar_modulo(monkeypatch, fake_sfn):
    monkeypatch.setenv("STATE_MACHINE_ARN", "arn:aws:states:us-east-1:123:stateMachine:pipeline")
    with mock.patch("boto3.client", return_value=fake_sfn):
        import iniciar_pipeline

        importlib.reload(iniciar_pipeline)
        return iniciar_pipeline


def _fake_sfn_ok():
    fake = mock.Mock()
    fake.exceptions.ExecutionAlreadyExists = _ExecutionAlreadyExists
    fake.start_execution.return_value = {
        "executionArn": "arn:aws:states:us-east-1:123:execution:pipeline:nome"
    }
    return fake


def test_inicia_execucao_com_nome_saneado_do_arquivo(monkeypatch):
    fake_sfn = _fake_sfn_ok()
    modulo = _carregar_modulo(monkeypatch, fake_sfn)

    resposta = modulo.handler(
        _evento("meu-bucket", "raw/dtcc_zip/CFTC_CUMULATIVE_RATES_2026_10_02.zip"),
        context=None,
    )

    assert resposta["duplicado"] is False
    kwargs = fake_sfn.start_execution.call_args.kwargs
    assert kwargs["name"] == "CFTC_CUMULATIVE_RATES_2026_10_02-zip"
    assert "/" not in kwargs["name"]
    assert kwargs["stateMachineArn"] == "arn:aws:states:us-east-1:123:stateMachine:pipeline"


def test_input_da_execucao_e_o_proprio_evento(monkeypatch):
    import json

    fake_sfn = _fake_sfn_ok()
    modulo = _carregar_modulo(monkeypatch, fake_sfn)
    evento = _evento("meu-bucket", "raw/dtcc_zip/arquivo.zip")

    modulo.handler(evento, context=None)

    kwargs = fake_sfn.start_execution.call_args.kwargs
    assert json.loads(kwargs["input"]) == evento


def test_execution_already_exists_e_tratado_como_sucesso_nao_erro(monkeypatch):
    fake_sfn = mock.Mock()
    fake_sfn.exceptions.ExecutionAlreadyExists = _ExecutionAlreadyExists
    fake_sfn.start_execution.side_effect = _ExecutionAlreadyExists()
    modulo = _carregar_modulo(monkeypatch, fake_sfn)

    resposta = modulo.handler(
        _evento("meu-bucket", "raw/dtcc_zip/arquivo.zip"), context=None
    )

    assert resposta["duplicado"] is True


def test_nome_de_execucao_nunca_passa_de_80_caracteres(monkeypatch):
    fake_sfn = _fake_sfn_ok()
    modulo = _carregar_modulo(monkeypatch, fake_sfn)
    nome_longo = "A" * 200 + ".zip"

    modulo.handler(_evento("meu-bucket", f"raw/dtcc_zip/{nome_longo}"), context=None)

    kwargs = fake_sfn.start_execution.call_args.kwargs
    assert len(kwargs["name"]) <= 80
