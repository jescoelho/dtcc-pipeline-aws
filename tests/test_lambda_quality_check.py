"""Testes da Lambda de checagem de qualidade (lambda/quality_check.py),
com S3 e SNS mockados -- sem precisar de AWS de verdade.

Evento no formato "S3 Object Created" do EventBridge (não o {"Records":
[...]} do S3 direto) -- ver o docstring de lambda/quality_check.py."""
import csv
import importlib
import io
import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))

COLUNAS_OK = [
    "Dissemination Identifier",
    "Original Dissemination Identifier",
    "Action type",
    "Event timestamp",
]


def _evento_eventbridge(bucket: str, key: str) -> dict:
    return {
        "detail-type": "Object Created",
        "source": "aws.s3",
        "detail": {"bucket": {"name": bucket}, "object": {"key": key}},
    }


def _csv_bytes(linhas: list, colunas: list) -> bytes:
    buf = io.StringIO()
    escritor = csv.DictWriter(buf, fieldnames=colunas)
    escritor.writeheader()
    escritor.writerows(linhas)
    return buf.getvalue().encode("utf-8-sig")


def _mock_clients(corpo_csv: bytes, execution_id: str = "exec-fixo-teste", historico: list = None):
    """historico: lista de dicts (registros de quality_check já
    gravados em dias anteriores) que _media_historica deve "encontrar"
    ao listar logs/execucoes/. Default vazio = sem histórico ainda, que
    é o caso normal da maioria dos testes (a checagem de volume
    relativo simplesmente não roda)."""
    historico = historico or []
    historico_por_key = {}
    contents = []
    for i, registro in enumerate(historico):
        key = f"logs/execucoes/dt=2026-01-{i + 1:02d}/{i}.json"
        historico_por_key[key] = json.dumps(registro).encode("utf-8")
        contents.append({"Key": key})

    fake_s3 = mock.Mock()

    def _get_object(Bucket, Key):
        if Key in historico_por_key:
            return {"Body": io.BytesIO(historico_por_key[Key])}
        return {"Body": io.BytesIO(corpo_csv)}

    fake_s3.get_object.side_effect = _get_object
    fake_s3.head_object.return_value = {"Metadata": {"execution-id": execution_id}}

    fake_paginator = mock.Mock()
    fake_paginator.paginate.return_value = [{"Contents": contents}] if contents else []
    fake_s3.get_paginator.return_value = fake_paginator

    fake_sns = mock.Mock()

    def _client(nome, *a, **kw):
        return {"s3": fake_s3, "sns": fake_sns}[nome]

    return fake_s3, fake_sns, _client


def _carregar_modulo(monkeypatch, client_fn):
    monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123:jessica-dtcclab-alertas")
    with mock.patch("boto3.client", side_effect=client_fn):
        import quality_check

        importlib.reload(quality_check)
        return quality_check


def test_csv_valido_nao_publica_alerta(monkeypatch):
    corpo = _csv_bytes(
        [
            {
                "Dissemination Identifier": "1",
                "Original Dissemination Identifier": "",
                "Action type": "NEWT",
                "Event timestamp": "2026-10-02T19:00:00",
            }
        ],
        COLUNAS_OK,
    )
    fake_s3, fake_sns, client_fn = _mock_clients(corpo)
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler(_evento_eventbridge("bucket-teste", "raw/dtcc/a.csv"), context=None)

    assert resposta["checado"]["problemas"] == []
    assert resposta["checado"]["linhas"] == 1
    fake_sns.publish.assert_not_called()


def test_csv_vazio_publica_alerta(monkeypatch):
    corpo = _csv_bytes([], COLUNAS_OK)
    fake_s3, fake_sns, client_fn = _mock_clients(corpo)
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler(_evento_eventbridge("bucket-teste", "raw/dtcc/vazio.csv"), context=None)

    assert resposta["checado"]["linhas"] == 0
    assert resposta["checado"]["problemas"]
    fake_sns.publish.assert_called_once()


def test_coluna_ausente_publica_alerta(monkeypatch):
    colunas_sem_action_type = [c for c in COLUNAS_OK if c != "Action type"]
    corpo = _csv_bytes(
        [
            {
                "Dissemination Identifier": "1",
                "Original Dissemination Identifier": "",
                "Event timestamp": "2026-10-02T19:00:00",
            }
        ],
        colunas_sem_action_type,
    )
    fake_s3, fake_sns, client_fn = _mock_clients(corpo)
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler(
        _evento_eventbridge("bucket-teste", "raw/dtcc/sem_coluna.csv"), context=None
    )

    assert any("colunas ausentes" in p for p in resposta["checado"]["problemas"])
    fake_sns.publish.assert_called_once()


def test_action_type_inesperado_publica_alerta(monkeypatch):
    corpo = _csv_bytes(
        [
            {
                "Dissemination Identifier": "1",
                "Original Dissemination Identifier": "",
                "Action type": "XXXX",
                "Event timestamp": "2026-10-02T19:00:00",
            }
        ],
        COLUNAS_OK,
    )
    fake_s3, fake_sns, client_fn = _mock_clients(corpo)
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler(
        _evento_eventbridge("bucket-teste", "raw/dtcc/tipo_estranho.csv"), context=None
    )

    assert any(
        "Action type inesperado" in p for p in resposta["checado"]["problemas"]
    )
    fake_sns.publish.assert_called_once()


def test_cada_evento_e_checado_independentemente(monkeypatch):
    corpo = _csv_bytes(
        [
            {
                "Dissemination Identifier": "1",
                "Original Dissemination Identifier": "",
                "Action type": "NEWT",
                "Event timestamp": "2026-10-02T19:00:00",
            }
        ],
        COLUNAS_OK,
    )
    fake_s3, fake_sns, client_fn = _mock_clients(corpo)
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta_1 = modulo.handler(_evento_eventbridge("b", "raw/dtcc/a.csv"), context=None)
    resposta_2 = modulo.handler(_evento_eventbridge("b", "raw/dtcc/b.csv"), context=None)

    assert resposta_1["checado"]["csv"] == "s3://b/raw/dtcc/a.csv"
    assert resposta_2["checado"]["csv"] == "s3://b/raw/dtcc/b.csv"


def test_registra_execucao_na_tabela_de_controle_mesmo_sem_problema(monkeypatch):
    corpo = _csv_bytes(
        [
            {
                "Dissemination Identifier": "1",
                "Original Dissemination Identifier": "",
                "Action type": "NEWT",
                "Event timestamp": "2026-10-02T19:00:00",
            }
        ],
        COLUNAS_OK,
    )
    fake_s3, fake_sns, client_fn = _mock_clients(corpo, execution_id="exec-abc")
    modulo = _carregar_modulo(monkeypatch, client_fn)

    modulo.handler(_evento_eventbridge("meu-bucket", "raw/dtcc/a.csv"), context=None)

    fake_s3.put_object.assert_called_once()
    kwargs = fake_s3.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "meu-bucket"
    assert kwargs["Key"].startswith("logs/execucoes/dt=")
    registro = json.loads(kwargs["Body"])
    assert registro["origem"] == "quality_check"
    assert registro["status"] == "ok"
    assert registro["linhas"] == 1
    assert registro["execution_id"] == "exec-abc"


def test_registro_na_tabela_de_controle_marca_status_problema(monkeypatch):
    corpo = _csv_bytes([], COLUNAS_OK)
    fake_s3, fake_sns, client_fn = _mock_clients(corpo)
    modulo = _carregar_modulo(monkeypatch, client_fn)

    modulo.handler(_evento_eventbridge("meu-bucket", "raw/dtcc/vazio.csv"), context=None)

    kwargs = fake_s3.put_object.call_args.kwargs
    registro = json.loads(kwargs["Body"])
    assert registro["status"] == "problema"
    assert registro["problemas"]


def test_volume_abaixo_do_historico_publica_alerta(monkeypatch):
    # Histórico de 5 dias com 100 linhas cada (média = 100); arquivo de
    # hoje vem com 1 linha só -- queda bem maior que os 50% tolerados.
    historico = [
        {"origem": "quality_check", "linhas": 100, "status": "ok"} for _ in range(5)
    ]
    corpo = _csv_bytes(
        [
            {
                "Dissemination Identifier": "1",
                "Original Dissemination Identifier": "",
                "Action type": "NEWT",
                "Event timestamp": "2026-10-02T19:00:00",
            }
        ],
        COLUNAS_OK,
    )
    fake_s3, fake_sns, client_fn = _mock_clients(corpo, historico=historico)
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler(_evento_eventbridge("meu-bucket", "raw/dtcc/a.csv"), context=None)

    assert any("volume muito abaixo do histórico" in p for p in resposta["checado"]["problemas"])
    fake_sns.publish.assert_called_once()


def test_volume_normal_comparado_ao_historico_nao_alerta(monkeypatch):
    # Histórico de 5 dias com 10 linhas cada (média = 10); arquivo de
    # hoje vem com 9 -- dentro da tolerância de 50%, não deve alertar.
    historico = [{"origem": "quality_check", "linhas": 10, "status": "ok"} for _ in range(5)]
    corpo = _csv_bytes(
        [
            {
                "Dissemination Identifier": str(i),
                "Original Dissemination Identifier": "",
                "Action type": "NEWT",
                "Event timestamp": "2026-10-02T19:00:00",
            }
            for i in range(9)
        ],
        COLUNAS_OK,
    )
    fake_s3, fake_sns, client_fn = _mock_clients(corpo, historico=historico)
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler(_evento_eventbridge("meu-bucket", "raw/dtcc/a.csv"), context=None)

    assert resposta["checado"]["problemas"] == []
    fake_sns.publish.assert_not_called()


def test_sem_historico_nao_roda_checagem_de_volume_relativo(monkeypatch):
    # Sem nenhum registro histórico (pipeline nos primeiros dias) --
    # mesmo um arquivo de 1 linha não deve ser comparado contra nada.
    corpo = _csv_bytes(
        [
            {
                "Dissemination Identifier": "1",
                "Original Dissemination Identifier": "",
                "Action type": "NEWT",
                "Event timestamp": "2026-10-02T19:00:00",
            }
        ],
        COLUNAS_OK,
    )
    fake_s3, fake_sns, client_fn = _mock_clients(corpo, historico=[])
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler(_evento_eventbridge("meu-bucket", "raw/dtcc/a.csv"), context=None)

    assert resposta["checado"]["problemas"] == []


def test_historico_ignora_registros_de_outras_origens(monkeypatch):
    # trigger_bronze e glue_job também gravam em logs/execucoes/, mas
    # não têm "linhas" com o mesmo significado (ou nem têm o campo) --
    # a média não deve se confundir com eles.
    historico = [
        {"origem": "trigger_bronze", "job_run_id": "jr_1"},
        {"origem": "glue_job", "status": "succeeded", "duracao_segundos": 90},
        {"origem": "quality_check", "linhas": 5, "status": "ok"},
    ]
    corpo = _csv_bytes([], COLUNAS_OK)
    fake_s3, fake_sns, client_fn = _mock_clients(corpo, historico=historico)
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler(_evento_eventbridge("meu-bucket", "raw/dtcc/vazio.csv"), context=None)

    # média histórica efetiva = 5 (só o registro de quality_check conta)
    # -- arquivo vazio (0 linhas) fica bem abaixo disso, mas o problema
    # de "arquivo vazio" já cobre esse caso; o importante aqui é que não
    # explodiu tentando ler "linhas" dos registros sem esse campo.
    assert any("volume muito abaixo do histórico" in p for p in resposta["checado"]["problemas"])
