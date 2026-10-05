"""Hook PreToolUse do subagente aplicador-adequacao (ver .claude/agents/).

Impõe por código o que as instruções do agente só pedem:
  - Edit/Write: só dentro de .claude/outputs/<nome>/, nunca em .revisao/
    (handoff do orquestrador) nem em .git/.
  - Bash: allowlist estrita -- só `python -m pytest ...` e
    `python .../validar_terraform.py ...`, opcionalmente precedidos de
    `cd <OUT_DIR> &&`. Qualquer outro comando, ou qualquer metacaractere de
    shell fora desse formato, é bloqueado. Isso cobre terraform, aws, git,
    pip, curl, redirecionamento e encadeamento.

Protocolo: lê o JSON do evento em stdin; bloqueia com exit 2 e a razão em
stderr (o agente vê a razão e pode ajustar). Falha fechada: entrada
ilegível também bloqueia.
"""
import json
import os
import re
import shlex
import sys

FERRAMENTAS_ESCRITA = {"Edit", "Write", "NotebookEdit"}
METACARACTERES = re.compile(r"[;&|<>`$\n\r(){}*?\\]")


def bloquear(motivo):
    print(f"[guarda_aplicador] bloqueado: {motivo}", file=sys.stderr)
    sys.exit(2)


def norm(caminho):
    return os.path.normcase(os.path.realpath(caminho))


def raiz_outputs(projeto):
    return norm(os.path.join(projeto, ".claude", "outputs"))


def dentro_de_saida(caminho, base_cwd, outputs):
    """True se `caminho` está em outputs/<nome>/... fora de .revisao e .git."""
    absoluto = caminho if os.path.isabs(caminho) else os.path.join(base_cwd, caminho)
    real = norm(absoluto)
    if not real.startswith(outputs + os.sep):
        return False
    partes = real[len(outputs) + 1:].split(os.sep)
    if len(partes) < 2:  # precisa ser um arquivo/pasta DENTRO de <nome>/
        return False
    return not ({".revisao", ".git"} & set(partes[1:]))


def validar_escrita(entrada, cwd, outputs):
    caminho = entrada.get("file_path") or entrada.get("notebook_path")
    if not caminho:
        bloquear("escrita sem file_path")
    if not dentro_de_saida(caminho, cwd, outputs):
        bloquear(f"escrita fora de .claude/outputs/<nome>/ (ou em .revisao/.git): {caminho}")


def argumentos_ok(tokens, cwd, outputs):
    """Flags passam; qualquer outro argumento que pareça caminho tem de
    resolver dentro de uma pasta de saída."""
    for t in tokens:
        if t.startswith("-"):
            continue
        if "/" in t or "\\" in t or t.endswith((".py", ".tf", ".yaml")) or t == ".":
            if not dentro_de_saida(t, cwd, outputs) and norm(os.path.join(cwd, t)) != norm(cwd):
                bloquear(f"argumento fora da pasta de saída: {t}")


def validar_bash(entrada, cwd, outputs):
    comando = (entrada.get("command") or "").strip()
    if not comando:
        bloquear("comando vazio")

    # Único encadeamento aceito: "cd <dir> && <comando permitido>".
    cwd_efetivo = cwd
    if " && " in comando:
        pre, _, comando = comando.partition(" && ")
        pre_tokens = shlex.split(pre, posix=True)
        if len(pre_tokens) != 2 or pre_tokens[0] != "cd":
            bloquear("encadeamento só é aceito como 'cd <pasta de saída> && ...'")
        destino = os.path.join(cwd, pre_tokens[1])
        if not (dentro_de_saida(destino, cwd, outputs) or any(
                norm(destino) == norm(os.path.join(outputs, d))
                for d in os.listdir(outputs))):
            bloquear(f"cd fora de .claude/outputs/<nome>/: {pre_tokens[1]}")
        cwd_efetivo = destino

    if METACARACTERES.search(comando):
        bloquear("metacaractere de shell no comando")
    tokens = shlex.split(comando, posix=True)
    if not tokens or tokens[0] not in ("python", "python3", "py"):
        bloquear(f"comando não permitido: {tokens[0] if tokens else comando}")

    resto = tokens[1:]
    if resto[:2] == ["-m", "pytest"]:
        argumentos_ok(resto[2:], cwd_efetivo, outputs)
        return
    if resto and os.path.basename(resto[0]) == "validar_terraform.py":
        script = os.path.join(cwd_efetivo, resto[0])
        esperado = norm(os.path.join(
            os.path.dirname(outputs), "skills", "nova-fonte-dados",
            "scripts", "validar_terraform.py"))
        if norm(script) != esperado:
            bloquear("validar_terraform.py só do caminho oficial da skill")
        argumentos_ok(resto[1:], cwd_efetivo, outputs)
        return
    bloquear("só 'python -m pytest' e 'python .../validar_terraform.py' são permitidos")


def main():
    try:
        evento = json.load(sys.stdin)
        ferramenta = evento["tool_name"]
        entrada = evento.get("tool_input") or {}
        projeto = os.environ.get("CLAUDE_PROJECT_DIR") or evento.get("cwd") or os.getcwd()
        cwd = evento.get("cwd") or projeto
        outputs = raiz_outputs(projeto)
        if ferramenta in FERRAMENTAS_ESCRITA:
            validar_escrita(entrada, cwd, outputs)
        elif ferramenta == "Bash":
            validar_bash(entrada, cwd, outputs)
        # outras ferramentas (Read/Glob/Grep): sem restrição aqui
    except SystemExit:
        raise
    except Exception as erro:  # falha fechada
        bloquear(f"erro ao avaliar o evento ({type(erro).__name__}: {erro})")


if __name__ == "__main__":
    main()
