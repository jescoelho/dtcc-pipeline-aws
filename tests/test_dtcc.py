"""Testes da Bronze de DTCC PPD, contra um recorte REAL (300 linhas) do
arquivo Cumulative de 02/10/2026 baixado do dashboard do DTCC -- não é
dado sintético. O recorte vive em tests/fixtures/dtcc_cumulative_sample.csv.
"""
import csv
from pathlib import Path

import pyarrow.dataset as ds

from dtcc.bronze import ingest_file

FIXTURE = Path(__file__).parent / "fixtures" / "dtcc_cumulative_sample.csv"


def _ler_fixture_cru() -> list[dict]:
    with open(FIXTURE, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_bronze_preserva_todas_as_linhas(tmp_path):
    linhas_originais = _ler_fixture_cru()

    resultado = ingest_file(FIXTURE, tmp_path / "bronze")

    assert resultado["linhas"] == len(linhas_originais) == 300
    tabela = ds.dataset(str(tmp_path / "bronze"), partitioning="hive").to_table().to_pandas()
    assert len(tabela) == 300


def test_bronze_nao_converte_nenhum_valor(tmp_path):
    """Bronze = sem interpretação. Os valores devem chegar como string,
    idênticos ao que estava no CSV -- inclusive os vazios."""
    linhas_originais = _ler_fixture_cru()
    ingest_file(FIXTURE, tmp_path / "bronze")

    tabela = ds.dataset(str(tmp_path / "bronze"), partitioning="hive").to_table().to_pandas()
    tabela = tabela.sort_values("line_number").reset_index(drop=True)

    # amostra: primeira linha real, campo que sabemos existir no header
    primeira = tabela.iloc[0]
    assert primeira["Dissemination Identifier"] == linhas_originais[0]["Dissemination Identifier"]
    assert primeira["Action type"] == linhas_originais[0]["Action type"]
    # campo tipicamente vazio em NEWT -- deve permanecer string vazia, não NaN/None
    if linhas_originais[0]["Original Dissemination Identifier"] == "":
        assert primeira["Original Dissemination Identifier"] == ""


def test_bronze_adiciona_proveniencia(tmp_path):
    ingest_file(FIXTURE, tmp_path / "bronze")

    tabela = ds.dataset(str(tmp_path / "bronze"), partitioning="hive").to_table().to_pandas()

    assert set(tabela["arquivo_origem"]) == {"dtcc_cumulative_sample.csv"}
    assert set(tabela["line_number"]) == set(range(300))
    assert tabela["ingerido_em"].notna().all()


def test_cadeia_de_eventos_do_mesmo_trade_existe_no_fixture():
    """Confirma que o recorte de 300 linhas preserva ao menos uma cadeia
    NEWT -> MODI (o padrão de CDC real que motivou este módulo)."""
    linhas = _ler_fixture_cru()
    ids_originais = {r["Dissemination Identifier"] for r in linhas if r["Action type"] == "NEWT"}
    tem_modificacao_referenciando_o_recorte = any(
        r["Action type"] != "NEWT" and r["Original Dissemination Identifier"] in ids_originais
        for r in linhas
    )
    assert tem_modificacao_referenciando_o_recorte
