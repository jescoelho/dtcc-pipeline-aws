"""Teste do hook guarda_aplicador.py: python .claude/hooks/test_guarda_aplicador.py"""
import json, os, subprocess, sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOOK = os.path.join(RAIZ, ".claude", "hooks", "guarda_aplicador.py")
OUT = ".claude/outputs/ice_ticker"
VT = ".claude/skills/nova-fonte-dados/scripts/validar_terraform.py"

# (esperado_bloqueio, ferramenta, entrada)
CASOS = [
    (False, "Edit", {"file_path": f"{OUT}/terraform/modules/fonte/glue.tf"}),
    (False, "Write", {"file_path": os.path.join(RAIZ, OUT, "lambda", "x.py")}),
    (True, "Edit", {"file_path": "terraform/modules/fonte/glue.tf"}),            # repo principal
    (True, "Write", {"file_path": f"{OUT}/../../skills/nova-fonte-dados/SKILL.md"}),  # traversal
    (True, "Write", {"file_path": f"{OUT}/.revisao/rodada-1/achados.json"}),
    (True, "Write", {"file_path": ".claude/outputs/solto.txt"}),                  # fora de <nome>/
    (True, "Write", {"file_path": "C:/Windows/temp/x"}),
    (True, "Edit", {}),
    (False, "Bash", {"command": f"cd {OUT} && python -m pytest -q"}),
    (False, "Bash", {"command": f"python {VT} {OUT}/terraform --contrato {OUT}/config/fontes/ice_ticker.yaml"}),
    (True, "Bash", {"command": "terraform apply -auto-approve"}),
    (True, "Bash", {"command": "aws s3 ls"}),
    (True, "Bash", {"command": "git commit -am x"}),
    (True, "Bash", {"command": "pip install requests"}),
    (True, "Bash", {"command": f"python -m pytest -q; rm -rf /"}),
    (True, "Bash", {"command": f"python -m pytest > /etc/x"}),
    (True, "Bash", {"command": f"cd terraform && python -m pytest"}),
    (True, "Bash", {"command": "python -m pytest ../../../terraform"}),
    (True, "Bash", {"command": "python -c \"import os\""}),
    (True, "Bash", {"command": f"python outro.py"}),
    (True, "Bash", {"command": "python .claude/outputs/ice_ticker/scripts/validar_terraform.py x"}),  # script falso
]

def rodar(ferramenta, entrada):
    ev = {"tool_name": ferramenta, "tool_input": entrada, "cwd": RAIZ}
    env = dict(os.environ, CLAUDE_PROJECT_DIR=RAIZ)
    return subprocess.run([sys.executable, HOOK], input=json.dumps(ev), text=True,
                          capture_output=True, env=env).returncode

falhas = 0
for bloqueia, ferr, ent in CASOS:
    rc = rodar(ferr, ent)
    ok = (rc == 2) == bloqueia
    falhas += not ok
    print(("ok  " if ok else "FALHA"), ferr, json.dumps(ent)[:90], "-> rc", rc)
# falha fechada: lixo no stdin
rc = subprocess.run([sys.executable, HOOK], input="nao-json", text=True, capture_output=True).returncode
print("ok  " if rc == 2 else "FALHA", "stdin ilegível -> rc", rc); falhas += rc != 2
sys.exit(1 if falhas else 0)
