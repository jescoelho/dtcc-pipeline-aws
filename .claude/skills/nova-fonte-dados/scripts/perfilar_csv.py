#!/usr/bin/env python3
"""Perfila uma ou mais amostras CSV para inferir o contrato de fonte.

Ferramenta geral dos passos 1 e 5 da skill nova-fonte-dados. Substitui a
análise feita à mão em cada fonte nova. Só biblioteca padrão.

Para o conjunto de arquivos, informa:
  - formato: encoding/BOM, fim de linha, delimitador, nº de colunas, e se o
    header é idêntico em todos os arquivos (schema estável?);
  - por coluna: % preenchido, nº de valores distintos, valores mais comuns
    (se poucos distintos);
  - candidatas a `coluna_id`: 100% preenchidas e 100% únicas ENTRE todos os
    arquivos (a unicidade medida só num arquivo engana);
  - candidatas a `coluna_dominio`/`valores_dominio`: poucas categorias, quase
    sempre preenchidas;
  - colunas cujo nome o Parquet/Spark pode rejeitar (` ,;{}()\\n\\t=`; o
    espaço sozinho costuma passar);
  - volume por dia da semana, a partir da data no NOME do arquivo
    (--date-regex), com a razão entre o menor e o maior: evidência para ligar
    (ou não) `consistencia_sazonal` no contrato.

Nada aqui decide pelo usuário: imprime evidências; o contrato é escrito
pela skill (precedência: override > inferência > default).

Uso:
    python perfilar_csv.py arq1.csv arq2.csv ... [--delimiter ,] [--top 5]
        [--date-regex '(\\d{4})[-_](\\d{2})[-_](\\d{2})'] [--max-distintos 15]
"""
import argparse
import collections
import csv
import datetime
import re
import sys

# Windows: stdout em cp1252 corrompe acentos; força UTF-8 (sem efeito em Linux)
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

PARQUET_RUIM = re.compile(r"[,;{}()\n\t=]")


def detectar_delimitador(amostra: str) -> str:
    try:
        return csv.Sniffer().sniff(amostra, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("arquivos", nargs="+")
    ap.add_argument("--delimiter")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--max-distintos", type=int, default=15, help="até quantos distintos mostrar os valores")
    ap.add_argument("--date-regex", default=r"(\d{4})[-_](\d{2})[-_](\d{2})")
    a = ap.parse_args()

    headers, linhas_por_arq, datas = {}, {}, {}
    contagem = collections.defaultdict(collections.Counter)  # coluna -> valor -> n
    preenchidos = collections.Counter()
    total = 0
    formato = []

    for arq in a.arquivos:
        bruto = open(arq, "rb").read()
        bom = bruto[:3] == b"\xef\xbb\xbf"
        crlf = b"\r\n" in bruto
        try:
            texto = bruto.decode("utf-8-sig")
            enc = "utf-8"
        except UnicodeDecodeError:
            texto = bruto.decode("latin-1")
            enc = "latin-1 (NÃO é utf-8)"
        delim = a.delimiter or detectar_delimitador(texto[:20000])
        leitor = csv.DictReader(texto.splitlines(True) if False else __import__("io").StringIO(texto, newline=""), delimiter=delim)
        headers[arq] = tuple(leitor.fieldnames or [])
        n = 0
        for linha in leitor:
            n += 1
            for c, v in linha.items():
                if c is None:
                    continue
                v = v or ""
                if v.strip():
                    preenchidos[c] += 1
                contagem[c][v] += 1
        linhas_por_arq[arq] = n
        total += n
        formato.append((arq, enc, "BOM" if bom else "sem BOM", "CRLF" if crlf else "LF", repr(delim), len(headers[arq]), n))
        m = re.search(a.date_regex, arq)
        if m:
            try:
                datas[arq] = datetime.date(*map(int, m.groups()[:3]))
            except ValueError:
                pass

    print(f"== {len(a.arquivos)} arquivo(s), {total} linhas\n")
    for f in formato:
        print("  %s | %s | %s | %s | delim %s | %d colunas | %d linhas" % f)
    distintos_headers = set(headers.values())
    print(f"\nHeader idêntico em todos os arquivos: {'SIM' if len(distintos_headers) == 1 else 'NÃO (' + str(len(distintos_headers)) + ' variantes) -- schema instável'}")
    colunas = list(next(iter(headers.values())))
    if total == 0:
        print("Sem linhas de dados.")
        return 1

    print("\n== Colunas (preenchimento | distintos | valores mais comuns se poucos)")
    ids, dominios = [], []
    for c in colunas:
        cont = contagem[c]
        pre = preenchidos[c] / total
        dist = len(cont)
        extra = ""
        if dist <= a.max_distintos:
            extra = " ".join(f"{v!r}:{n}" for v, n in cont.most_common(a.top))
        print(f"  {c!r}: {pre:5.0%} | {dist} | {extra}")
        if pre == 1.0 and dist == total:
            ids.append(c)
        if 2 <= dist <= 12 and pre >= 0.95:
            dominios.append((c, sorted(v for v in cont if v)))

    print("\n== Candidatas a coluna_id (100% preenchida e 100% única entre TODOS os arquivos)")
    print("  " + (", ".join(map(repr, ids)) if ids else "nenhuma -- pergunte ao usuário (AskUserQuestion) ou use chave composta"))
    if len(a.arquivos) == 1:
        print("  (aviso: só 1 arquivo; unicidade entre dias não foi testada)")
    print("\n== Candidatas a coluna_dominio (2-12 categorias, >=95% preenchida)")
    for c, vals in dominios:
        print(f"  {c!r}: {vals}")
    ruins = [c for c in colunas if PARQUET_RUIM.search(c)]
    print("\n== Colunas com caractere que o Parquet/Spark pode recusar: " + (", ".join(map(repr, ruins)) if ruins else "nenhuma"))

    if datas:
        print("\n== Volume por dia (data tirada do nome do arquivo)")
        por_dia = sorted((d, linhas_por_arq[arq]) for arq, d in datas.items())
        for d, n in por_dia:
            print(f"  {d} {d.strftime('%a')}: {n}")
        fds = [n for d, n in por_dia if d.weekday() >= 5]
        util = [n for d, n in por_dia if d.weekday() < 5]
        if fds and util:
            razao = (sum(fds) / len(fds)) / (sum(util) / len(util))
            print(f"  média fim de semana / média dia útil = {razao:.2f}")
            if razao < 0.6 or razao > 1.6:
                print("  => diferença marcante: há evidência para consistencia_sazonal: true")
            else:
                print("  => sem diferença marcante: manter consistencia_sazonal: false (default)")
        else:
            print("  (amostra sem fim de semana E dia útil: sem evidência sazonal; manter false)")
    else:
        print("\n(sem data reconhecível no nome dos arquivos: volume por dia não calculado; use --date-regex)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
