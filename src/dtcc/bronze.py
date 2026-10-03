"""Bronze — ingestão crua dos relatórios Cumulative de PPD (Public Price
Dissemination) do DTCC.

Mesma filosofia da Bronze do COTAHIST (src/cotahist/bronze.py): guardar o
dado exatamente como chegou, sem converter tipo nenhum, só com metadado de
proveniência. A diferença é o formato de origem: COTAHIST é largura fixa
(exige recortar posições, que já é uma forma de interpretação, por isso lá
a Bronze guarda a linha crua inteira); CSV já vem com colunas delimitadas
pelo próprio arquivo, então dividir em colunas aqui não é interpretação —
é só reconhecer a estrutura que o arquivo já declara no cabeçalho. Nenhum
valor é convertido (tudo permanece string, inclusive campos vazios).

Cada linha do CSV é um EVENTO de dissseminação (Action type: NEWT, MODI,
CORR, TERM, EROR, REVI) referenciando a transação original via
'Original Dissemination Identifier'. A Bronze não resolve essa cadeia —
isso é trabalho da Silver. A Bronze só preserva os eventos como chegaram.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ENCODING = "utf-8-sig"  # arquivos do DTCC trazem BOM no início


def ingest_file(csv_path: str | Path, bronze_dir: str | Path) -> dict:
    """Lê um CSV Cumulative do DTCC PPD e grava em Parquet, sem tipar nada.

    Adiciona 3 colunas de proveniência: arquivo_origem, line_number,
    ingerido_em. Particiona por arquivo_origem, igual à Bronze do COTAHIST.
    """
    csv_path = Path(csv_path)
    bronze_dir = Path(bronze_dir)
    source_file = csv_path.name
    ingested_at = datetime.now(timezone.utc)

    with open(csv_path, encoding=ENCODING, newline="") as fh:
        reader = csv.DictReader(fh)
        colunas_originais = list(reader.fieldnames or [])
        linhas = list(reader)

    n = len(linhas)
    colunas = {c: [row.get(c, "") or "" for row in linhas] for c in colunas_originais}
    colunas["arquivo_origem"] = [source_file] * n
    colunas["line_number"] = list(range(n))
    colunas["ingerido_em"] = [ingested_at] * n

    table = pa.table(colunas)
    pq.write_to_dataset(
        table,
        root_path=str(bronze_dir),
        partition_cols=["arquivo_origem"],
        compression="snappy",
        existing_data_behavior="delete_matching",
    )
    return {
        "arquivo": source_file,
        "linhas": n,
        "colunas_originais": len(colunas_originais),
    }


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", required=True, help="caminho do CSV Cumulative")
    parser.add_argument("--out", required=True, help="diretório de saída da Bronze")
    args = parser.parse_args()
    print(json.dumps(ingest_file(args.raw, args.out), default=str))
