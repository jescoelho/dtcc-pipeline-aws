#!/usr/bin/env python3
"""
Baixa arquivos de uma origem HTTP que expõe listagem de diretório (estilo
Apache/nginx "Index of"), organizada em pastas previsíveis -- por exemplo
por subpasta, ano e mês. Ferramenta geral: nada aqui é específico de uma
fonte; tudo que varia vem de parâmetros.

Estratégia: em vez de adivinhar nomes de arquivo, lista cada diretório,
extrai os links reais, filtra por regex e baixa só o que ainda não existe
localmente (idempotente: pode rodar de novo ou numa agenda). O destino
espelha o caminho relativo à URL base.

Antes de usar contra um site, confirme que os termos de uso e o robots.txt
permitem acesso automatizado. A ferramenta identifica-se com um
User-Agent próprio por padrão, respeita 429/5xx com backoff e PARA ao
receber 401/403 (não insiste contra um bloqueio). Não implementa nada
para disfarçar o acesso; `--user-agent`/`--header` existem para origens
que exijam uma identificação específica ou autenticação legítima.

Placeholders de --path-template: {subdir} (cada item de --subdirs),
{ano}, {mes} (com zero à esquerda). Se o template não tiver {ano}, é uma
listagem única, sem iteração por mês.

Exemplos:
    # listar as subpastas da raiz
    python baixar_listagem.py --base-url https://host/ftp/dados/ --list-subdirs

    # subpasta/ano/mes/ARQ.AAAAMMDD.csv.zip, só os diários, a partir de 2025-01
    python baixar_listagem.py --base-url https://host/ftp/dados/ \\
        --subdirs classe_a classe_b --start 2025-01 --out ./dados \\
        --file-regex '^RT\\.[A-Z_]+\\.\\d{8}\\.csv\\.zip$'

    # diretório único, sem partição por data
    python baixar_listagem.py --base-url https://host/arquivos/ \\
        --path-template '' --file-regex '\\.csv$' --dry-run
"""
import argparse
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests

DEFAULT_UA = "baixar-listagem/1.0 (+uso pessoal; contato: veja --user-agent)"
HREF_RE = re.compile(r'href="([^"?#]+)"', re.I)
RETRY_STATUS = (429, 500, 502, 503, 504)
BLOCKED_STATUS = (401, 403)


class Bloqueado(Exception):
    """Origem recusou o acesso (401/403): não adianta tentar de novo."""


def get(session, url, retries, timeout, **kw):
    """GET com retry/backoff. Retorna Response, ou None se 404."""
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=timeout, **kw)
            if r.status_code == 404:
                return None
            if r.status_code in BLOCKED_STATUS:
                raise Bloqueado(f"HTTP {r.status_code} em {url}")
            if r.status_code in RETRY_STATUS:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except Bloqueado:
            raise
        except requests.RequestException as e:
            if attempt == retries:
                print(f"  ! falhou {url}: {e}", file=sys.stderr)
                raise
            time.sleep(2 ** attempt)


def parse_links(html):
    """Links relativos da listagem (ignora absolutos, '..' e ordenações ?C=)."""
    return [h for h in HREF_RE.findall(html) if not h.startswith(("/", "http", ".."))]


def parse_ym(s):
    """Converte 'AAAA-MM' em (ano, mes) como inteiros."""
    y, m = s.split("-")
    return int(y), int(m)


def month_iter(start, end):
    """Gera (ano, mes) de `start` a `end`, ambos inclusive; cada um é uma tupla (ano, mes)."""
    y, m = start
    while (y, m) <= end:
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def dir_urls(args, today):
    """Gera as URLs de diretório a listar a partir do template."""
    subdirs = args.subdirs or [""]
    iterar_meses = "{ano}" in args.path_template
    start = parse_ym(args.start)
    end = parse_ym(args.end) if args.end else (today.year, today.month)
    for sub in subdirs:
        if not iterar_meses:
            yield urljoin(args.base_url, args.path_template.format(subdir=sub))
            continue
        for y, m in month_iter(start, end):
            rel = args.path_template.format(subdir=sub, ano=y, mes=f"{m:02d}")
            yield urljoin(args.base_url, rel)


def build_parser():
    """Monta o ArgumentParser com todos os parâmetros da ferramenta (ver o docstring do módulo)."""
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True, help="URL base (termina em /)")
    ap.add_argument("--out", default="./download", help="pasta de destino")
    ap.add_argument("--subdirs", nargs="*", default=None,
                    help="valores de {subdir} no template (padrão: nenhum)")
    ap.add_argument("--path-template", default="{subdir}/{ano}/{mes}/",
                    help="caminho relativo à base; placeholders {subdir} {ano} {mes}")
    ap.add_argument("--file-regex", default=r".+\.[A-Za-z0-9]+$",
                    help="só baixa links cujo nome case com esta regex")
    ap.add_argument("--exclude-regex", default=None,
                    help="descarta links cujo nome case com esta regex")
    ap.add_argument("--start", default="2013-01", help="AAAA-MM inicial")
    ap.add_argument("--end", default=None, help="AAAA-MM final (padrão: mês atual)")
    ap.add_argument("--list-subdirs", action="store_true",
                    help="só lista as subpastas da URL base e sai")
    ap.add_argument("--dry-run", action="store_true",
                    help="lista o que baixaria, sem baixar")
    ap.add_argument("--sleep", type=float, default=0.5, help="pausa entre downloads (s)")
    ap.add_argument("--retries", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("--user-agent", default=DEFAULT_UA)
    ap.add_argument("--header", action="append", default=[], metavar="CHAVE=VALOR",
                    help="header extra (repetível), ex.: Authorization=Bearer ...")
    ap.add_argument("--ignore-robots", action="store_true",
                    help="não consulta robots.txt (padrão: consulta e respeita)")
    return ap


def pode_acessar(rp, ua, url):
    """True se `rp` (RobotFileParser, ou None) permite `ua` buscar `url`; sem
    robots.txt, permite.
    """
    return rp is None or rp.can_fetch(ua, url)


def carregar_robots(base_url, ua, timeout):
    """Baixa e interpreta o robots.txt da origem de `base_url`; devolve None se não
    existir ou falhar.
    """
    p = urlparse(base_url)
    url = f"{p.scheme}://{p.netloc}/robots.txt"
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": ua})
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    rp = robotparser.RobotFileParser()
    rp.parse(r.text.splitlines())
    return rp


def main(argv=None):
    """Lista as subpastas (--list-subdirs) ou percorre os diretórios, baixando os
    arquivos novos; para em 401/403.
    """
    args = build_parser().parse_args(argv)
    if not args.base_url.endswith("/"):
        args.base_url += "/"

    s = requests.Session()
    s.headers.update({"User-Agent": args.user_agent, "Accept": "*/*"})
    for h in args.header:
        k, _, v = h.partition("=")
        s.headers[k.strip()] = v.strip()

    rp = None if args.ignore_robots else carregar_robots(args.base_url, args.user_agent, args.timeout)

    try:
        if args.list_subdirs:
            r = get(s, args.base_url, args.retries, args.timeout)
            if r is None:
                sys.exit("Não consegui listar " + args.base_url)
            print("\n".join(sorted(h.strip("/") for h in parse_links(r.text) if h.endswith("/"))))
            return

        incluir = re.compile(args.file_regex)
        excluir = re.compile(args.exclude_regex) if args.exclude_regex else None
        out = Path(args.out)
        novos = existentes = 0

        for dir_url in dir_urls(args, date.today()):
            if not pode_acessar(rp, args.user_agent, dir_url):
                print(f"  ! robots.txt não permite {dir_url}; pulando", file=sys.stderr)
                continue
            r = get(s, dir_url, args.retries, args.timeout)
            if r is None:  # diretório inexistente (ex.: mês sem publicação)
                continue
            arquivos = sorted(f for f in parse_links(r.text)
                              if not f.endswith("/") and incluir.match(f)
                              and not (excluir and excluir.match(f)))
            rel_dir = dir_url[len(args.base_url):]
            for f in arquivos:
                file_url = urljoin(dir_url, f)
                dest = out / rel_dir / f
                if dest.exists() and dest.stat().st_size > 0:
                    existentes += 1
                    continue
                if args.dry_run:
                    print(f"[dry-run] {file_url}")
                    continue
                if not pode_acessar(rp, args.user_agent, file_url):
                    print(f"  ! robots.txt não permite {file_url}; pulando", file=sys.stderr)
                    continue
                resp = get(s, file_url, args.retries, args.timeout)
                if resp is None:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_suffix(dest.suffix + ".part")
                tmp.write_bytes(resp.content)
                tmp.rename(dest)  # escrita atômica: nunca fica arquivo pela metade
                novos += 1
                print(f"+ {rel_dir}{f} ({len(resp.content)/1024:.0f} KB)")
                time.sleep(args.sleep)
    except Bloqueado as e:
        sys.exit(f"Acesso recusado ({e}). Parando: verifique os termos de uso da "
                 "origem e peça acesso autorizado em vez de insistir.")

    print(f"\nconcluído: {novos} novos, {existentes} já existiam -> {out.resolve()}")


if __name__ == "__main__":
    main()
