#!/usr/bin/env python3
"""Sonda uma URL de origem de dados ANTES de qualquer download em massa.

Ferramenta geral do passo 1 da skill nova-fonte-dados: responde, só com
GETs de recursos estáticos (nunca POST, nunca chama endpoint de dados nem
pede token), o que a URL é e o que precisa ser decidido:

  - robots.txt da origem (existência e regras `Disallow`);
  - tipo da URL: arquivo direto, listagem de diretório ("Index of"),
    resposta JSON/API, ou página HTML/SPA;
  - para SPA: os scripts da página são lidos e procurados por caminhos de
    API (ex. /app/webpi/exportTicks) e URLs absolutas -- é assim que se
    descobre a API real que a página consome;
  - menções a termos de uso / disclaimer / licença (páginas e bundles),
    com os links, para o usuário ler. Termos que restrinjam cópia ou
    acesso automatizado => perguntar ao usuário (AskUserQuestion) se ele
    tem autorização, antes de seguir.

Identifica-se com User-Agent próprio e não tenta contornar bloqueio: se a
origem responder 401/403/429, o relatório diz isso e para.

Uso:
    python sondar_origem.py <url> [--max-scripts 6] [--user-agent TEXTO]
"""
import argparse
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

# Windows: stdout em cp1252 corrompe acentos; força UTF-8 (sem efeito em Linux)
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

UA_PADRAO = "dtcc-pipeline-aws/1.0 (sondagem de origem)"
LIMITE_BYTES = 3_000_000
PALAVRAS_TERMOS = re.compile(
    r"disclaimer|terms[ -_]?(of[ -_]?(use|service))?|license|licen[cs]e|"
    r"copyright|redistribut|reproduc|robots",
    re.I,
)
CAMINHO_API = re.compile(
    r"""["'](/[A-Za-z0-9_\-./]{3,}?(?:api|webpi|rest|v\d+|export|download|"""
    r"""data|csv|json|feed|ticks?|query|list)[A-Za-z0-9_\-./?=&]*)["']""",
    re.I,
)
URL_ABSOLUTA = re.compile(r"""https?://[A-Za-z0-9_\-./%?=&:#~+]+""")
IGNORAR_URL = re.compile(
    r"w3\.org|schema\.org|googleapis\.com/css|fonts\.g|cookielaw|onetrust|"
    r"github\.com|npmjs|mozilla\.org|reactjs\.org|angular\.io",
    re.I,
)


def baixar(url, ua, limite=LIMITE_BYTES):
    """GET; devolve (status, content_type, corpo bytes, url_final) ou (codigo, '', b'', url) em erro HTTP."""
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read(limite), r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", b"", url
    except Exception as e:  # rede, TLS, DNS
        return None, f"erro: {e}", b"", url


def classificar(status, ctype, corpo):
    ct = ctype.lower()
    if status is None or status >= 400:
        return "inacessivel"
    if any(t in ct for t in ("csv", "zip", "gzip", "excel", "spreadsheet", "octet-stream", "parquet")):
        return "arquivo_direto"
    if "json" in ct:
        return "api_json"
    if "xml" in ct and "html" not in ct:
        return "xml"
    texto = corpo[:200_000].decode("utf-8", errors="replace")
    if re.search(r"<title>\s*Index of", texto, re.I) or len(re.findall(r"<a href=", texto, re.I)) > 5 and "Parent Directory" in texto:
        return "listagem_diretorio"
    if "<script" in texto.lower():
        return "pagina_html_com_scripts"
    return "pagina_html"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("url")
    ap.add_argument("--max-scripts", type=int, default=6)
    ap.add_argument("--user-agent", default=UA_PADRAO)
    a = ap.parse_args()
    url = a.url if "://" in a.url else "https://" + a.url
    p = urllib.parse.urlparse(url)
    raiz = f"{p.scheme}://{p.netloc}"

    print(f"== Sondagem de {url}\n")
    st, ct, corpo, _ = baixar(raiz + "/robots.txt", a.user_agent, 100_000)
    if st == 200:
        regras = [l.strip() for l in corpo.decode("utf-8", "replace").splitlines() if l.lower().startswith(("disallow", "user-agent", "crawl-delay"))]
        print(f"robots.txt: 200 ({len(regras)} regras relevantes)")
        for l in regras[:15]:
            print("   ", l)
    else:
        print(f"robots.txt: HTTP {st} (sem regras declaradas)" if st == 404 else f"robots.txt: {st} {ct}")

    st, ct, corpo, final = baixar(url, a.user_agent)
    tipo = classificar(st, ct, corpo)
    print(f"\nURL: HTTP {st} | {ct} | {len(corpo)} bytes lidos | final: {final}")
    print(f"Tipo: {tipo}")
    if st in (401, 403, 429):
        print("\n!! A origem recusou/limitou o acesso. NÃO tente contornar; consulte o usuário (AskUserQuestion).")
        return 2
    if tipo == "inacessivel":
        return 1

    texto = corpo.decode("utf-8", errors="replace")
    achados_termos = set()
    apis, absolutas = {}, {}

    def varrer(origem, conteudo):
        for m in PALAVRAS_TERMOS.finditer(conteudo):
            achados_termos.add(m.group(0).lower())
        for m in CAMINHO_API.finditer(conteudo):
            apis.setdefault(m.group(1), origem)
        for m in URL_ABSOLUTA.finditer(conteudo):
            if not IGNORAR_URL.search(m.group(0)):
                absolutas.setdefault(m.group(0).rstrip(".,;)"), origem)

    if tipo.startswith("pagina"):
        varrer("html", texto)
        links_termos = {
            urllib.parse.urljoin(final, h)
            for h in re.findall(r"""href=["']([^"']+)["']""", texto, re.I)
            if PALAVRAS_TERMOS.search(h)
        }
        scripts = [urllib.parse.urljoin(final, s) for s in re.findall(r"""<script[^>]+src=["']([^"']+)["']""", texto, re.I)]
        proprios = [s for s in scripts if urllib.parse.urlparse(s).netloc == p.netloc]
        print(f"\nScripts da página: {len(scripts)} ({len(proprios)} do mesmo host) -- lendo até {a.max_scripts}")
        # app > libs: o bundle da aplicação costuma ter o nome do app; vendor/polyfill/min por último
        proprios.sort(key=lambda s: bool(re.search(r"vendor|polyfill|failsafe|wijmo|jquery|\.min\.|techplatform|lib", s, re.I)))
        for s in proprios[: a.max_scripts]:
            st2, _, c2, _ = baixar(s, a.user_agent)
            if st2 == 200:
                varrer(s.rsplit("/", 1)[-1], c2.decode("utf-8", errors="replace"))
        if links_termos:
            print("\nLinks com cara de termos/licença (LEIA antes de baixar):")
            for l in sorted(links_termos)[:10]:
                print("   ", l)
    elif tipo == "listagem_diretorio":
        print("\nListagem de diretório: use scripts/baixar_listagem.py (--list-subdirs, --dry-run).")
        varrer("html", texto)
    elif tipo == "api_json":
        print("\nPrévia JSON:", texto[:300].replace("\n", " "))

    if apis:
        print("\nCaminhos que parecem API (origem = onde apareceu):")
        for c, o in sorted(apis.items())[:25]:
            print(f"    {c}   <- {o}")
    if absolutas:
        print("\nURLs absolutas encontradas (filtradas):")
        for c, o in sorted(absolutas.items())[:15]:
            print(f"    {c}   <- {o}")
    if achados_termos:
        print(f"\nPalavras de termos/licença encontradas: {', '.join(sorted(achados_termos))}")
        print("=> Há texto de termos. Leia-o; se restringir cópia/redistribuição/acesso automatizado,\n   pergunte ao usuário (AskUserQuestion) se ele tem autorização antes de automatizar.")
    print("\nPróximo passo: ler os endpoints candidatos no código-fonte (nada foi chamado além de GETs estáticos);\nsó então baixar UMA amostra pequena, com User-Agent identificado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
