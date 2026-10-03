"""Testes da Lambda de descompactação (lambda/unzip_dtcc.py), contra um
.zip real construído a partir do fixture de 300 linhas -- sem precisar de
S3 de verdade, usando mocks que imitam get_object/put_object."""
import json
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))

FIXTURE = Path(__file__).parent / "fixtures" / "dtcc_cumulative_sample.csv"


def _zip_do_fixture() -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(FIXTURE, arcname="dtcc_cumulative_sample.csv")
    return buf.getvalue()


def _s3_falso(conteudo_zip: bytes):
    store = {("bucket-teste", "raw/dtcc_zip/arquivo.zip"): conteudo_zip}
    put_calls = []

    fake = mock.Mock()
    fake.get_object.side_effect = lambda Bucket, Key: {
        "Body": mock.Mock(read=mock.Mock(return_value=store[(Bucket, Key)]))
    }

    def _put_object(Bucket, Key, Body, **kwargs):
        store[(Bucket, Key)] = Body
        put_calls.append({"Bucket": Bucket, "Key": Key, "Body": Body, **kwargs})

    fake.put_object.side_effect = _put_object
    return fake, store, put_calls


def test_descompacta_e_grava_csv_identico_ao_original(monkeypatch):
    monkeypatch.setenv("RAW_PREFIX", "raw/dtcc/")
    with mock.patch("boto3.client") as mock_client:
        fake_s3, store, put_calls = _s3_falso(_zip_do_fixture())
        mock_client.return_value = fake_s3

        import unzip_dtcc
        import importlib
        importlib.reload(unzip_dtcc)

        resultado = unzip_dtcc._processar("bucket-teste", "raw/dtcc_zip/arquivo.zip")

        assert resultado["destino"] == "raw/dtcc/dtcc_cumulative_sample.csv"
        csv_gravado = store[("bucket-teste", "raw/dtcc/dtcc_cumulative_sample.csv")]
        csv_original = FIXTURE.read_bytes()
        assert csv_gravado == csv_original


def test_execution_id_vai_no_metadado_do_csv_e_no_registro_de_controle(monkeypatch):
    monkeypatch.setenv("RAW_PREFIX", "raw/dtcc/")
    with mock.patch("boto3.client") as mock_client:
        fake_s3, store, put_calls = _s3_falso(_zip_do_fixture())
        mock_client.return_value = fake_s3

        import unzip_dtcc
        import importlib
        importlib.reload(unzip_dtcc)

        resultado = unzip_dtcc._processar("bucket-teste", "raw/dtcc_zip/arquivo.zip")

        csv_put = next(c for c in put_calls if c["Key"] == "raw/dtcc/dtcc_cumulative_sample.csv")
        assert csv_put["Metadata"]["execution-id"] == resultado["execution_id"]

        registro_put = next(c for c in put_calls if c["Key"].startswith("logs/execucoes/dt="))
        registro = json.loads(registro_put["Body"])
        assert registro["origem"] == "unzip_dtcc"
        assert registro["execution_id"] == resultado["execution_id"]
        assert registro["status"] == "descompactado"
        assert registro["zip"] == "s3://bucket-teste/raw/dtcc_zip/arquivo.zip"
        assert registro["csv"] == "s3://bucket-teste/raw/dtcc/dtcc_cumulative_sample.csv"


def test_handler_processa_todos_os_records_do_evento(monkeypatch):
    monkeypatch.setenv("RAW_PREFIX", "raw/dtcc/")
    with mock.patch("boto3.client") as mock_client:
        fake_s3, store, put_calls = _s3_falso(_zip_do_fixture())
        mock_client.return_value = fake_s3

        import unzip_dtcc
        import importlib
        importlib.reload(unzip_dtcc)

        evento = {
            "Records": [
                {"s3": {"bucket": {"name": "bucket-teste"}, "object": {"key": "raw/dtcc_zip/arquivo.zip"}}}
            ]
        }
        resposta = unzip_dtcc.handler(evento, context=None)

        assert len(resposta["processados"]) == 1
        assert resposta["processados"][0]["bytes"] == len(FIXTURE.read_bytes())


def test_zip_sem_csv_ou_com_mais_de_um_levanta_erro(monkeypatch):
    monkeypatch.setenv("RAW_PREFIX", "raw/dtcc/")
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("nao_eh_csv.txt", "qualquer coisa")

    with mock.patch("boto3.client") as mock_client:
        fake_s3, _, _ = _s3_falso(buf.getvalue())
        mock_client.return_value = fake_s3

        import unzip_dtcc
        import importlib
        importlib.reload(unzip_dtcc)

        try:
            unzip_dtcc._processar("bucket-teste", "raw/dtcc_zip/arquivo.zip")
            assert False, "deveria ter levantado ValueError"
        except ValueError as e:
            assert "esperava exatamente 1" in str(e)
