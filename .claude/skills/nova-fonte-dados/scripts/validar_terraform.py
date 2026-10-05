#!/usr/bin/env python3
"""Checagem heurística do Terraform gerado no passo 6/8 do procedimento
(ver SKILL.md) -- existe porque este ambiente normalmente não tem o
binário `terraform` instalado nem acesso de rede pra instalar (ver
docs/DECISOES.md), e porque a extração do módulo `terraform/modules/fonte`
e outras mudanças anteriores neste repositório já fizeram essa mesma
checagem à mão, por scripts descartáveis reescritos a cada vez. Este
script substitui essa reinvenção -- não substitui `terraform validate`
nem `terraform plan`, que ainda precisam ser rodados localmente antes de
qualquer `apply`.

O que verifica (via regex, não um parser HCL completo -- pode ter falsos
positivos/negativos em casos incomuns: blocos dinâmicos, splat
expressions, ou a sequência de caracteres aparecendo dentro de uma string
literal):

1. Balanceamento de chaves/parênteses/colchetes em cada arquivo .tf.
2. Toda referência `var.*`, `module.<nome>.*`, `aws_*.*`/`data.*.*`
   resolve a algo declarado nos próprios arquivos analisados.
3. (Opcional, com --contrato e --modulo) Todo campo acessado via
   `var.fonte.*` dentro dos arquivos do módulo existe no contrato YAML
   informado, e todo campo do YAML é referenciado em algum lugar do
   módulo -- nenhum dos dois lados fica com entradas órfãs.

Uso:
    python validar_terraform.py <diretorio_ou_arquivos.tf...>
    python validar_terraform.py terraform/modules/fonte/*.tf \
        --contrato config/fontes/<nome>.yaml

Saída: um relatório por categoria; termina com código de saída != 0 se
achar algum problema, pra poder ser usado como gate antes dos passos
seguintes do procedimento.
"""
import argparse
import re
import sys
from pathlib import Path

# Windows: stdout em cp1252 corrompe acentos; força UTF-8 (sem efeito em Linux)
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

try:
    import yaml
except ImportError:
    yaml = None

RE_RESOURCE_DECL = re.compile(r'\b(resource|data)\s+"([\w]+)"\s+"([\w]+)"\s*\{')
RE_BLOCK_DECL = re.compile(r'\b(variable|module|output|locals)\s*(?:"([\w]+)")?\s*\{')
RE_LOCAL_ASSIGN = re.compile(r'^\s*([\w]+)\s*=', re.MULTILINE)

RE_VAR_REF = re.compile(r'\bvar\.([\w]+)')
RE_MODULE_REF = re.compile(r'\bmodule\.([\w]+)\.([\w]+)')
RE_RESOURCE_REF = re.compile(r'\b(?:data\.)?(aws_[\w]+)\.([\w]+)\b')
RE_FONTE_FIELD = re.compile(r'\bvar\.fonte\.([\w]+)')

IGNORAR_PSEUDO_RECURSOS = {"aws_caller_identity", "aws_iam_policy_document", "aws_region"}


def coletar_arquivos(args_paths):
    """Expande diretórios em seus *.tf e devolve só os arquivos .tf que existem, na
    ordem informada.
    """
    arquivos = []
    for p in args_paths:
        path = Path(p)
        if path.is_dir():
            arquivos.extend(sorted(path.glob("*.tf")))
        else:
            arquivos.append(path)
    return [a for a in arquivos if a.suffix == ".tf" and a.exists()]


def checar_balanceamento(texto, nome_arquivo):
    """Confere o fechamento de {}, () e [] fora de strings em `texto`; devolve a lista
    de problemas (vazia se ok).
    """
    problemas = []
    pares = {"{": "}", "(": ")", "[": "]"}
    abre = set(pares.keys())
    fecha = {v: k for k, v in pares.items()}
    pilha = []
    dentro_string = False
    anterior = ""
    for i, ch in enumerate(texto):
        if ch == '"' and anterior != "\\":
            dentro_string = not dentro_string
        if not dentro_string:
            if ch in abre:
                pilha.append(ch)
            elif ch in fecha:
                if not pilha or pilha[-1] != fecha[ch]:
                    problemas.append(
                        f"{nome_arquivo}: '{ch}' sem abertura correspondente (posição {i})"
                    )
                else:
                    pilha.pop()
        anterior = ch
    if pilha:
        problemas.append(f"{nome_arquivo}: {len(pilha)} abertura(s) sem fechamento ({pilha})")
    return problemas


def coletar_declaracoes(textos_por_arquivo):
    """Varre todo o conjunto de arquivos e devolve os nomes declarados:
    recursos (tipo.nome), blocos nomeados (variable/module/output) e
    locals."""
    recursos = set()
    variaveis = set()
    modulos = set()
    outputs = set()
    locals_declarados = set()

    for texto in textos_por_arquivo.values():
        for _, tipo, nome in RE_RESOURCE_DECL.findall(texto):
            recursos.add(f"{tipo}.{nome}")
        for bloco, nome in RE_BLOCK_DECL.findall(texto):
            if bloco == "variable" and nome:
                variaveis.add(nome)
            elif bloco == "module" and nome:
                modulos.add(nome)
            elif bloco == "output" and nome:
                outputs.add(nome)

        # locals{} pode ter várias atribuições dentro de um único bloco;
        # aproximação: toda atribuição "nome = ..." dentro de um bloco
        # locals é um local declarado. Não tenta isolar o corpo exato do
        # bloco -- assume que arquivos deste módulo não têm outro tipo de
        # bloco com a mesma forma "nome = valor" solto no nível errado.
        for bloco_match in re.finditer(r'\blocals\s*\{', texto):
            inicio = bloco_match.end()
            profundidade = 1
            fim = inicio
            while fim < len(texto) and profundidade > 0:
                if texto[fim] == "{":
                    profundidade += 1
                elif texto[fim] == "}":
                    profundidade -= 1
                fim += 1
            corpo = texto[inicio:fim]
            for nome in RE_LOCAL_ASSIGN.findall(corpo):
                locals_declarados.add(nome)

    return {
        "recursos": recursos,
        "variaveis": variaveis,
        "modulos": modulos,
        "outputs": outputs,
        "locals": locals_declarados,
    }


def checar_referencias(textos_por_arquivo, declaracoes):
    """Confere se var.*, module.*.* e aws_*.* referenciados foram declarados em
    `declaracoes`; devolve a lista de problemas.
    """
    problemas = []
    for nome_arquivo, texto in textos_por_arquivo.items():
        for nome in RE_VAR_REF.findall(texto):
            if nome not in declaracoes["variaveis"]:
                problemas.append(
                    f"{nome_arquivo}: var.{nome} referenciada, mas nenhuma "
                    f"'variable \"{nome}\"' foi declarada nos arquivos analisados"
                )
        for nome_modulo, _saida in RE_MODULE_REF.findall(texto):
            if nome_modulo not in declaracoes["modulos"]:
                problemas.append(
                    f"{nome_arquivo}: module.{nome_modulo}.* referenciado, mas "
                    f"nenhum 'module \"{nome_modulo}\"' foi declarado nos arquivos "
                    f"analisados (ok se o módulo estiver declarado num arquivo fora "
                    f"desta checagem, ex. main.tf analisado separadamente)"
                )
        for tipo, nome in RE_RESOURCE_REF.findall(texto):
            if tipo in IGNORAR_PSEUDO_RECURSOS:
                continue
            achou = any(
                r.endswith(f".{nome}") and tipo in r for r in declaracoes["recursos"]
            )
            if not achou:
                problemas.append(
                    f"{nome_arquivo}: {tipo}.{nome} referenciado, mas não declarado "
                    f"como resource/data nos arquivos analisados (ok se vier de um "
                    f"recurso compartilhado fora do módulo, ex. main.tf)"
                )
    return problemas


def checar_contrato(textos_por_arquivo, caminho_contrato):
    """Cruza os campos var.fonte.* usados nos .tf com as chaves do YAML em
    `caminho_contrato`, nos dois sentidos; devolve a lista de problemas.
    """
    if yaml is None:
        return ["pyyaml não instalado -- pulei a checagem contra o contrato YAML"]
    if not caminho_contrato.exists():
        return [f"contrato não encontrado: {caminho_contrato}"]

    contrato = yaml.safe_load(caminho_contrato.read_text()) or {}
    campos_contrato = set(contrato.keys())

    campos_referenciados = set()
    for texto in textos_por_arquivo.values():
        campos_referenciados.update(RE_FONTE_FIELD.findall(texto))

    problemas = []
    faltando_no_contrato = campos_referenciados - campos_contrato
    nao_usados_no_modulo = campos_contrato - campos_referenciados
    for campo in sorted(faltando_no_contrato):
        problemas.append(
            f"var.fonte.{campo} é referenciado no módulo, mas não existe em {caminho_contrato.name}"
        )
    for campo in sorted(nao_usados_no_modulo):
        problemas.append(
            f"{caminho_contrato.name} define '{campo}', mas nenhum arquivo analisado "
            f"referencia var.fonte.{campo} -- campo sem uso, ou a checagem não olhou "
            f"todos os arquivos relevantes"
        )
    return problemas


def main():
    """Lê os argumentos, roda as checagens e imprime o relatório; sai com código 1 se
    houver problemas e 2 se não achar .tf.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("caminhos", nargs="+", help="diretórios e/ou arquivos .tf a analisar")
    parser.add_argument("--contrato", type=Path, help="YAML do contrato de fonte, pra cruzar com var.fonte.*")
    args = parser.parse_args()

    arquivos = coletar_arquivos(args.caminhos)
    if not arquivos:
        print("Nenhum arquivo .tf encontrado nos caminhos informados.", file=sys.stderr)
        sys.exit(2)

    textos_por_arquivo = {str(a): a.read_text() for a in arquivos}

    problemas = []

    print(f"Analisando {len(arquivos)} arquivo(s) .tf...\n")

    for nome_arquivo, texto in textos_por_arquivo.items():
        problemas.extend(checar_balanceamento(texto, nome_arquivo))

    declaracoes = coletar_declaracoes(textos_por_arquivo)
    problemas.extend(checar_referencias(textos_por_arquivo, declaracoes))

    if args.contrato:
        problemas.extend(checar_contrato(textos_por_arquivo, args.contrato))

    if problemas:
        print(f"{len(problemas)} possível(is) problema(s):\n")
        for p in problemas:
            print(f"  - {p}")
        print(
            "\nLembre-se: esta checagem é heurística (regex, não um parser HCL) e "
            "NÃO substitui `terraform validate`/`terraform plan` rodados localmente."
        )
        sys.exit(1)

    print(
        "Nenhum problema encontrado por esta checagem heurística. Ainda assim, "
        "rode `terraform validate`/`terraform plan` localmente antes de qualquer apply."
    )


if __name__ == "__main__":
    main()
