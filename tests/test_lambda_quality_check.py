"""Testes da Lambda de checagem de qualidade (lambda/quality_check.py),
com S3 e SNS mockados -- sem precisar de AWS de verdade.

Evento no formato "S3 Object Created" do EventBridge (não o {"Records":
[...]} do S3 direto) -- ver o docstring de lambda/quality_check.py.

Desde 03/10/2026 esta Lambda não checa mais schema nem domínio (Action
type) -- isso passou pro AWS Glue Data Quality (ver
glue/bronze_ingest.py e o docstring do módulo). Os testes que existiam
pra essas duas checagens foram removidos daqui; o que resta é só
contagem de linhas e comparação com o histórico.

Desde 04/10/2026, a comparação com o histórico tenta primeiro a
consistência sazonal (mesmo dia da semana) -- os testes que usam
`_mock_clients` (ignora o prefixo pedido, sempre devolve o mesmo
histórico) continuam valendo porque a média não muda com repetição; os
testes de consistência sazonal propriamente dita usam
`_mock_clients_por_data`, que respeita a data real pedida em cada
`Prefix`, pra poder diferenciar "dias do mesmo dia da semana" de
"qualquer um dos últimos N dias corridos"."""
import csv
import importlib
import io
import json
import sys
from datetime import datetime, timedelta, timezone
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


def _mock_clients_por_data(corpo_csv: bytes, registros_por_dia: dict, execution_id: str = "exec-fixo-teste"):
    """Variante de _mock_clients que RESPEITA a data pedida em cada
    Prefix (`logs/execucoes/dt=<data>/`), em vez de devolver sempre o
    mesmo histórico independente do argumento -- necessário pra testar
    de verdade a filtragem por dia da semana (_media_historica com
    mesmo_dia_semana=True só consulta os dias que batem).

    registros_por_dia: {date: [registro, ...]} -- só os dias presentes
    aqui têm algo pra listar; qualquer outro dia pedido devolve vazio."""
    arquivos_por_dia = {}
    for dia, registros in registros_por_dia.items():
        arquivos_por_dia[dia] = {
            f"logs/execucoes/dt={dia.isoformat()}/{i}.json": json.dumps(registro).encode("utf-8")
            for i, registro in enumerate(registros)
        }

    fake_s3 = mock.Mock()
    fake_s3.head_object.return_value = {"Metadata": {"execution-id": execution_id}}

    def _get_object(Bucket, Key):
        for arquivos in arquivos_por_dia.values():
            if Key in arquivos:
                return {"Body": io.BytesIO(arquivos[Key])}
        return {"Body": io.BytesIO(corpo_csv)}

    fake_s3.get_object.side_effect = _get_object

    def _paginate(Bucket, Prefix):
        dia = datetime.strptime(Prefix.split("dt=")[1].rstrip("/"), "%Y-%m-%d").date()
        arquivos = arquivos_por_dia.get(dia, {})
        contents = [{"Key": k} for k in arquivos]
        return [{"Contents": contents}] if contents else []

    fake_paginator = mock.Mock()
    fake_paginator.paginate.side_effect = _paginate
    fake_s3.get_paginator.return_value = fake_paginator

    fake_sns = mock.Mock()

    def _client(nome, *a, **kw):
        return {"s3": fake_s3, "sns": fake_sns}[nome]

    return fake_s3, fake_sns, _client


def _carregar_modulo(monkeypatch, client_fn, env: dict = None):
    monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123:jessica-dtcclab-alertas")
    for chave, valor in (env or {}).items():
        monkeypatch.setenv(chave, valor)
    with mock.patch("boto3.client", side_effect=client_fn):
        import quality_check

        importlib.reload(quality_check)
        return quality_check


def test_csv_normal_sem_historico_nao_publica_alerta(monkeypatch):
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


def test_registra_execucao_na_tabela_de_controle(monkeypatch):
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
    # mesmo um arquivo pequeno não deve ser comparado contra nada.
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
    fake_sns.publish.assert_not_called()


def test_historico_ignora_registros_de_outras_origens(monkeypatch):
    # trigger_bronze, glue_job e glue_data_quality também gravam em
    # logs/execucoes/, mas não têm "linhas" com o mesmo significado (ou
    # nem têm o campo) -- a média não deve se confundir com eles.
    historico = [
        {"origem": "trigger_bronze", "job_run_id": "jr_1"},
        {"origem": "glue_job", "status": "succeeded", "duracao_segundos": 90},
        {"origem": "glue_data_quality", "status": "ok", "regras": "[]"},
        {"origem": "quality_check", "linhas": 5, "status": "ok"},
    ]
    corpo = _csv_bytes([], COLUNAS_OK)
    fake_s3, fake_sns, client_fn = _mock_clients(corpo, historico=historico)
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler(_evento_eventbridge("meu-bucket", "raw/dtcc/vazio.csv"), context=None)

    # média histórica efetiva = 5 (só o registro de quality_check conta)
    # -- arquivo vazio (0 linhas) fica bem abaixo disso. O importante
    # aqui é que não explodiu tentando ler "linhas" dos registros sem
    # esse campo.
    assert any("volume muito abaixo do histórico" in p for p in resposta["checado"]["problemas"])


# ---------- Consistência sazonal (04/10/2026) ----------

def _registro(linhas: int) -> dict:
    return {"origem": "quality_check", "linhas": linhas, "status": "ok"}


def test_consistencia_sazonal_usa_media_do_mesmo_dia_da_semana(monkeypatch):
    # Dias que caem no mesmo dia da semana de hoje (há 1, 2, 3 e 4
    # semanas): volume alto (50). Os outros dias da janela "flat"
    # (ontem..há 6 dias): volume baixo (5). Se o código usasse a média
    # simples (dias 1..7: 6x5 + 1x50 = 100/7 ~= 14,3, limite ~7,1), um
    # arquivo de 20 linhas NÃO alertaria. Usando a sazonal (média 50,
    # limite 25), 20 linhas ALERTA -- só dispara se a sazonal for a
    # baseline de fato usada, não a simples.
    hoje = datetime.now(timezone.utc).date()
    registros_por_dia = {hoje - timedelta(days=7 * n): [_registro(50)] for n in range(1, 5)}
    registros_por_dia.update({hoje - timedelta(days=d): [_registro(5)] for d in range(1, 7)})

    corpo = _csv_bytes(
        [
            {
                "Dissemination Identifier": str(i),
                "Original Dissemination Identifier": "",
                "Action type": "NEWT",
                "Event timestamp": "2026-10-02T19:00:00",
            }
            for i in range(20)
        ],
        COLUNAS_OK,
    )
    fake_s3, fake_sns, client_fn = _mock_clients_por_data(corpo, registros_por_dia)
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler(_evento_eventbridge("meu-bucket", "raw/dtcc/a.csv"), context=None)

    problema = next(p for p in resposta["checado"]["problemas"] if "volume muito abaixo" in p)
    assert "mesmo dia da semana" in problema
    assert "50" in problema
    fake_sns.publish.assert_called_once()


def test_consistencia_sazonal_desligada_usa_media_simples(monkeypatch):
    # Mesmos dados do teste anterior, mas CONSISTENCIA_SAZONAL=false --
    # deve voltar a usar a média simples (dias 1..7, ~14,3) e NÃO
    # alertar com 20 linhas (bem acima do limite ~7,1), provando que o
    # desligamento por contrato de fonte realmente tem efeito.
    hoje = datetime.now(timezone.utc).date()
    registros_por_dia = {hoje - timedelta(days=7 * n): [_registro(50)] for n in range(1, 5)}
    registros_por_dia.update({hoje - timedelta(days=d): [_registro(5)] for d in range(1, 7)})

    corpo = _csv_bytes(
        [
            {
                "Dissemination Identifier": str(i),
                "Original Dissemination Identifier": "",
                "Action type": "NEWT",
                "Event timestamp": "2026-10-02T19:00:00",
            }
            for i in range(20)
        ],
        COLUNAS_OK,
    )
    fake_s3, fake_sns, client_fn = _mock_clients_por_data(corpo, registros_por_dia)
    modulo = _carregar_modulo(monkeypatch, client_fn, env={"CONSISTENCIA_SAZONAL": "false"})

    resposta = modulo.handler(_evento_eventbridge("meu-bucket", "raw/dtcc/a.csv"), context=None)

    assert resposta["checado"]["problemas"] == []
    fake_sns.publish.assert_not_called()


def test_sem_ocorrencias_sazonais_cai_para_media_simples(monkeypatch):
    # Nenhum histórico no mesmo dia da semana (há 1, 2, 3, 4 semanas) --
    # só nos outros dias da janela "flat" (ontem..há 6 dias, volume 30).
    # A sazonal deve devolver None e cair pra média simples (30), que
    # detecta a queda normalmente -- a sazonal nunca deixa a checagem
    # sem baseline nenhuma por falta de dado sazonal.
    hoje = datetime.now(timezone.utc).date()
    registros_por_dia = {hoje - timedelta(days=d): [_registro(30)] for d in range(1, 7)}

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
    fake_s3, fake_sns, client_fn = _mock_clients_por_data(corpo, registros_por_dia)
    modulo = _carregar_modulo(monkeypatch, client_fn)

    resposta = modulo.handler(_evento_eventbridge("meu-bucket", "raw/dtcc/a.csv"), context=None)

    problema = next(p for p in resposta["checado"]["problemas"] if "volume muito abaixo" in p)
    assert "dias corridos" in problema
    assert "mesmo dia da semana" not in problema
    fake_sns.publish.assert_called_once()


def test_semanas_historico_sazonal_e_parametrizavel(monkeypatch):
    # Só 2 ocorrências do mesmo dia da semana dentro de uma janela de 2
    # semanas (SEMANAS_HISTORICO_SAZONAL=2) -- a 3ª e 4ª ocorrência
    # (há 3 e 4 semanas, volume muito diferente) não devem entrar na
    # média, confirmando que o parâmetro do contrato de fonte é
    # respeitado, não um "4" cravado no código.
    hoje = datetime.now(timezone.utc).date()
    registros_por_dia = {
        hoje - timedelta(days=7): [_registro(50)],
        hoje - timedelta(days=14): [_registro(50)],
        hoje - timedelta(days=21): [_registro(5000)],  # fora da janela de 2 semanas
        hoje - timedelta(days=28): [_registro(5000)],  # fora da janela de 2 semanas
    }

    corpo = _csv_bytes(
        [
            {
                "Dissemination Identifier": str(i),
                "Original Dissemination Identifier": "",
                "Action type": "NEWT",
                "Event timestamp": "2026-10-02T19:00:00",
            }
            for i in range(20)
        ],
        COLUNAS_OK,
    )
    fake_s3, fake_sns, client_fn = _mock_clients_por_data(corpo, registros_por_dia)
    modulo = _carregar_modulo(monkeypatch, client_fn, env={"SEMANAS_HISTORICO_SAZONAL": "2"})

    resposta = modulo.handler(_evento_eventbridge("meu-bucket", "raw/dtcc/a.csv"), context=None)

    # média sazonal restrita às 2 semanas = 50 (não contaminada pelos
    # 5000 de 3/4 semanas atrás, fora da janela) -- 20 linhas, limite
    # 25, deve alertar.
    problema = next(p for p in resposta["checado"]["problemas"] if "volume muito abaixo" in p)
    assert "mesmo dia da semana" in problema
    assert "50" in problema
    fake_sns.publish.assert_called_once()
