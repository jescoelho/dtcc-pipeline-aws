"""Testes da Lambda de ingestão agendada (lambda/ingerir_cumulative.py),
com S3 e SNS mockados -- sem bucket público de verdade do DTCC. Confirma
o nome de arquivo montado a partir do padrão do contrato de fonte, a
cópia (CopyObject) entre buckets, o registro na tabela de controle e o
alerta por SNS quando a cópia falha (ex.: arquivo do dia ainda não
publicado)."""
import importlib
import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))

ENV = {
    "ORIGEM_BUCKET": "kgc0418-tdw-data-0",
    "ORIGEM_PADRAO": "{fonte}/eod/{fonte_upper}_CUMULATIVE_{classe_ativo}_{data_underscore}.zip",
    "ZIP_PREFIX": "raw/dtcc_zip/",
    "CLASSE_ATIVO_DEFAULT": "RATES",
    "FONTE_DEFAULT": "cftc",
    "BUCKET": "meu-bucket",
    "SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:123:jessica-dtcclab-alertas",
    "NOME_FONTE": "dtcc",
}


def _carregar_modulo(monkeypatch, client_fn, env=None):
    for chave, valor in {**ENV, **(env or {})}.items():
        monkeypatch.setenv(chave, valor)
    with mock.patch("boto3.client", side_effect=client_fn):
        import ingerir_cumulative

        importlib.reload(ingerir_cumulative)
        return ingerir_cumulative


def _mock_clients(copy_falha: Exception = None):
    fake_s3 = mock.Mock()
    if copy_falha:
        fake_s3.copy_object.side_effect = copy_falha
    fake_sns = mock.Mock()

    def _client(nome, *a, **kw):
        return {"s3": fake_s3, "sns": fake_sns}[nome]

    return fake_s3, fake_sns, _client


def test_monta_nome_do_arquivo_a_partir_do_contrato_e_copia(monkeypatch):
    fake_s3, fake_sns, client_fn = _mock_clients()
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler({"data": "2026-10-02"}, context=None)

    fake_s3.copy_object.assert_called_once_with(
        Bucket="meu-bucket",
        Key="raw/dtcc_zip/CFTC_CUMULATIVE_RATES_2026_10_02.zip",
        CopySource={
            "Bucket": "kgc0418-tdw-data-0",
            "Key": "cftc/eod/CFTC_CUMULATIVE_RATES_2026_10_02.zip",
        },
    )
    assert resposta["destino"] == "s3://meu-bucket/raw/dtcc_zip/CFTC_CUMULATIVE_RATES_2026_10_02.zip"
    fake_sns.publish.assert_not_called()


def test_usa_data_de_hoje_quando_nao_especificada(monkeypatch):
    from datetime import datetime, timezone

    fake_s3, _, client_fn = _mock_clients()
    modulo = _carregar_modulo(monkeypatch, client_fn)

    modulo.handler({}, context=None)

    hoje_underscore = datetime.now(timezone.utc).strftime("%Y_%m_%d")
    chamada = fake_s3.copy_object.call_args.kwargs
    assert hoje_underscore in chamada["Key"]


def test_classe_ativo_e_fonte_sobrepostos_pelo_evento(monkeypatch):
    fake_s3, _, client_fn = _mock_clients()
    modulo = _carregar_modulo(monkeypatch, client_fn)

    modulo.handler({"data": "2026-10-02", "classe_ativo": "CREDITS", "fonte": "sec"}, context=None)

    chamada = fake_s3.copy_object.call_args.kwargs
    assert chamada["CopySource"]["Key"] == "sec/eod/SEC_CUMULATIVE_CREDITS_2026_10_02.zip"


def test_registra_execucao_na_tabela_de_controle(monkeypatch):
    fake_s3, _, client_fn = _mock_clients()
    modulo = _carregar_modulo(monkeypatch, client_fn)

    modulo.handler({"data": "2026-10-02"}, context=None)

    fake_s3.put_object.assert_called_once()
    kwargs = fake_s3.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "meu-bucket"
    assert kwargs["Key"].startswith("logs/execucoes/dt=")
    registro = json.loads(kwargs["Body"])
    assert registro["origem"] == "ingerir_cumulative"
    assert registro["status"] == "copiado"
    assert "CFTC_CUMULATIVE_RATES_2026_10_02.zip" in registro["zip"]


def test_falha_na_copia_alerta_por_sns_e_propaga_erro(monkeypatch):
    erro = Exception("NoSuchKey: arquivo de hoje ainda não publicado")
    fake_s3, fake_sns, client_fn = _mock_clients(copy_falha=erro)
    modulo = _carregar_modulo(monkeypatch, client_fn)

    try:
        modulo.handler({"data": "2026-10-02"}, context=None)
        assert False, "deveria ter propagado a exceção"
    except Exception as capturada:
        assert capturada is erro

    fake_sns.publish.assert_called_once()
    kwargs = fake_sns.publish.call_args.kwargs
    assert "ingestão agendada falhou" in kwargs["Subject"]
    assert "CFTC_CUMULATIVE_RATES_2026_10_02.zip" in kwargs["Message"]
    fake_s3.put_object.assert_not_called()  # não registra sucesso numa falha
