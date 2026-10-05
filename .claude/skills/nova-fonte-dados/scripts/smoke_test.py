#!/usr/bin/env python3
"""Autoteste dos scripts da skill nova-fonte-dados (sem rede).

Roda `--help` de cada ferramenta e exercita `perfilar_csv.py` e
`validar_terraform.py` com dados sintéticos mínimos, conferindo a saída.
Não toca em nenhuma origem real nem no repositório. Rode após alterar
qualquer script (passo 10 do procedimento).

Uso: python smoke_test.py      # código de saída 0 = tudo certo
"""
import subprocess
import sys
import tempfile
from pathlib import Path

# Windows: stdout em cp1252 corrompe acentos; força UTF-8 (sem efeito em Linux)
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

AQUI = Path(__file__).parent
FERRAMENTAS = ["sondar_origem", "perfilar_csv", "baixar_listagem", "validar_terraform"]


def rodar(*args):
    return subprocess.run([sys.executable, str(AQUI / args[0]), *args[1:]],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def main():
    falhas = []

    def checar(nome, ok, detalhe=""):
        print(("ok   " if ok else "FALHA"), nome, detalhe if not ok else "")
        if not ok:
            falhas.append(nome)

    for f in FERRAMENTAS:
        r = rodar(f + ".py", "--help")
        checar(f"{f} --help", r.returncode == 0 and "usage" in r.stdout.lower(), r.stderr[-200:])

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "a_2026-01-05.csv").write_text("id,tipo,valor\n1,X,10\n2,Y,20\n", encoding="utf-8")
        (d / "b_2026-01-06.csv").write_text("id,tipo,valor\n3,X,30\n4,Y,40\n", encoding="utf-8")
        r = rodar("perfilar_csv.py", str(d / "a_2026-01-05.csv"), str(d / "b_2026-01-06.csv"))
        checar("perfilar_csv: roda e cita a coluna id", r.returncode == 0 and "id" in r.stdout, r.stderr[-200:])

        tf = d / "ok.tf"
        tf.write_text('variable "x" {}\nresource "aws_sns_topic" "t" { name = var.x }\n', encoding="utf-8")
        r = rodar("validar_terraform.py", str(tf))
        checar("validar_terraform: arquivo válido => 0", r.returncode == 0, r.stdout[-300:])

        ruim = d / "ruim.tf"
        ruim.write_text('resource "aws_sns_topic" "t" { name = var.nao_declarada \n', encoding="utf-8")
        r = rodar("validar_terraform.py", str(ruim))
        checar("validar_terraform: chave aberta + var inexistente => != 0", r.returncode != 0)

    print("\nTUDO CERTO" if not falhas else f"\n{len(falhas)} falha(s): {falhas}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
