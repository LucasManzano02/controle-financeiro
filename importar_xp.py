# -*- coding: utf-8 -*-
"""
importar_xp.py — Atualiza as posições da XP no app a partir do extrato mensal.

Uso:
    py importar_xp.py "C:/Users/lucas/Downloads/PosicaoDetalhada.xlsx"
    py importar_xp.py                     (procura o .xlsx mais recente em Downloads)

O que faz:
  1. Lê o "Posição Detalhada" da XP (.xlsx) — apenas stdlib, sem pip install.
  2. Reescreve APENAS as posições marcadas com src:'XP' no DEFAULT_PORTFOLIO
     de caixa-v9-local-backup.html. Posições de outras instituições
     (Inter, Avenue, Itaú, Dolarapp, Wise, BTG) ficam intactas.
  3. Incrementa PORT_VERSION para que o navegador recarregue a carteira
     na próxima abertura do site (o localStorage sozinho não se atualiza).

Depois de rodar, publique com:  py publicar.py
"""
import sys, os, re, glob, json, zipfile
import xml.etree.ElementTree as ET

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(BASE, "caixa-v9-local-backup.html")
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# ── Catálogo: nome no extrato da XP → metadados do app ───────────────────────
# A chave é um trecho em MAIÚSCULAS que identifica o produto no relatório.
# Produtos novos que não casarem aqui entram com ticker derivado do nome e
# GICS 'Diversificado' — o script avisa para você classificar depois.
CATALOGO = [
    ("SPX SEAHAWK",      dict(ticker="35.648.999/0001-64", gics="Diversificado",     cls="RF",           indexer="CDI+",   liquidity="D+30", rating="")),
    ("VINLAND",          dict(ticker="50.862.124/0001-54", gics="Utilities",         cls="RF",           indexer="IPCA+",  liquidity="D+30", rating="Isento IR")),
    ("TREND INVESTBACK", dict(ticker="37.910.132/0001-60", gics="Diversificado",     cls="RF",           indexer="CDI",    liquidity="D+1",  rating="")),
    ("KAPITALO K10",     dict(ticker="33.520.968/0001-06", gics="Diversificado",     cls="Multimercado", indexer="CDI+",   liquidity="D+30", rating="")),
    ("ARX EVEREST",      dict(ticker="35.789.436/0001-96", gics="Diversificado",     cls="RF",           indexer="CDI",    liquidity="D+1",  rating="")),
    ("MAPFRE",           dict(ticker="51.253.495/0001-00", gics="Diversificado",     cls="RF",           indexer="CDI",    liquidity="D+1",  rating="")),
    ("CRI JHSF",         dict(ticker="CRI-JHSF-2029",      gics="Real Estate",       cls="RF",           indexer="CDI",    liquidity="Vencimento", rating="Isento IR")),
    ("PERNAMBUCANAS",    dict(ticker="CDB-PERNAMBUCANAS",  gics="Financials",        cls="RF",           indexer="CDI",    liquidity="Vencimento", rating="")),
    ("CDB BANCO XP",     dict(ticker="CDB-XP-2028",        gics="Financials",        cls="RF",           indexer="CDI",    liquidity="Vencimento", rating="")),
    ("3TENTOS",          dict(ticker="CRA-3TENTOS-2032",   gics="Consumer Staples",  cls="RF",           indexer="CDI+",   liquidity="Vencimento", rating="Isento IR")),
    ("MINERVA",          dict(ticker="CRA-MINERVA-2030",   gics="Consumer Staples",  cls="RF",           indexer="CDI",    liquidity="Vencimento", rating="Isento IR")),
    ("TESOURO SELIC",    dict(ticker="LFT-2028",           gics="Diversificado",     cls="RF",           indexer="SELIC",  liquidity="Vencimento", rating="Tesouro Nacional")),
    ("TESOURO IPCA",     dict(ticker="NTNB-PRINC-2029",    gics="Diversificado",     cls="RF",           indexer="IPCA+",  liquidity="Vencimento", rating="Tesouro Nacional")),
]


# ── Leitura do .xlsx (stdlib) ───────────────────────────────────────────────
def _col(ref):
    n = 0
    for c in re.match(r"([A-Z]+)", ref).group(1):
        n = n * 26 + (ord(c) - 64)
    return n - 1


def ler_xlsx(path):
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{NS}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    sheet = wb.find(f"{NS}sheets")[0]
    tgt = rels[sheet.get(f"{RNS}id")].lstrip("/")
    if not tgt.startswith("xl/"):
        tgt = "xl/" + tgt
    rows = []
    for row in ET.fromstring(z.read(tgt)).iter(f"{NS}row"):
        cells = {}
        for c in row.findall(f"{NS}c"):
            v, t = c.find(f"{NS}v"), c.get("t")
            isel = c.find(f"{NS}is")
            if t == "inlineStr" and isel is not None:
                val = "".join(x.text or "" for x in isel.iter(f"{NS}t"))
            elif v is None:
                continue
            elif t == "s":
                val = shared[int(v.text)]
            else:
                val = v.text
            cells[_col(c.get("r"))] = val
        rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
    return rows


def money(s):
    s = str(s).replace("R$", "").replace("\xa0", " ").strip()
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


# ── Parsing do relatório ────────────────────────────────────────────────────
def parse_posicoes(rows):
    """Extrai posições de todas as seções que tenham cabeçalho com 'Posição'."""
    posicoes, secao = [], ""
    i = 0
    while i < len(rows):
        r = rows[i]
        txt = [str(c).strip() for c in r]
        joined = " ".join(txt)

        # título de seção (Fundos de Investimentos / Renda Fixa / Tesouro Direto…)
        if txt and txt[0] and len(txt[0]) > 3 and not any("Posição" in c for c in txt) \
           and "R$" in joined and sum(1 for c in txt if c) <= 3:
            secao = txt[0]

        # cabeçalho de tabela — colunas alinhadas com as linhas de dados;
        # a coluna 0 traz o rótulo do grupo ("42,3% | Pós-Fixado") e, nas
        # linhas seguintes, o nome do produto.
        if any(c.startswith("Posição") for c in txt):
            hdr = {c.strip(): j for j, c in enumerate(txt) if c.strip()}
            grupo = txt[0].split("|")[-1].strip() if txt else ""
            i += 1
            while i < len(rows):
                d = [str(c).strip() for c in rows[i]]
                if not d or not d[0]:
                    break
                nome = d[0]

                def get(*keys):
                    for k in keys:
                        for h, idx in hdr.items():
                            if h.startswith(k) and 0 <= idx < len(d):
                                return d[idx]
                    return ""

                pos = money(get("Posição"))
                apl = money(get("Valor aplicado"))
                if pos is None:
                    i += 1
                    continue
                if apl is None or apl == 0:
                    apl = pos
                posicoes.append(dict(
                    nome=nome, secao=secao, grupo=grupo, posicao=pos, aplicado=apl,
                    taxa=get("Taxa a mercado"), venc=get("Data vencimento"),
                    entrada=get("Data aplicação"),
                    qtd=money(get("Quantidade")), pu=money(get("Preço Unitário")),
                ))
                i += 1
            continue
        i += 1
    return posicoes


def meta_de(nome):
    up = nome.upper()
    for chave, m in CATALOGO:
        if chave in up:
            return m, True
    slug = re.sub(r"[^A-Z0-9]+", "-", up)[:24].strip("-")
    return dict(ticker=slug, gics="Diversificado", cls="RF",
                indexer="CDI", liquidity="Vencimento", rating=""), False


def js_str(s):
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def montar_bloco(posicoes, saldo, data_ref):
    """Gera as linhas JS das posições XP."""
    out, novos = [], []
    for n, p in enumerate(posicoes, 1):
        m, conhecido = meta_de(p["nome"])
        if not conhecido:
            novos.append(p["nome"])
        apl = p["aplicado"]
        rent = (p["posicao"] / apl - 1) * 100 if apl else 0
        venc = p["venc"] or "Indeterminado"
        if re.match(r"\d{2}/\d{2}/\d{4}$", venc):
            venc = venc[3:]  # dd/mm/yyyy → mm/yyyy
        out.append(
            f"  {{id:'xp{n:02d}', src:'XP', ticker:{js_str(m['ticker'])}, gics:{js_str(m['gics'])}, "
            f"product:{js_str(p['nome'])}, class:{js_str(m['cls'])},\n"
            f"   indexer:{js_str(m['indexer'])}, applied:{apl:.2f}, rent:{rent:.4f}, "
            f"rate:{js_str(p['taxa'])}, entryDate:{js_str(p['entrada'])}, maturity:{js_str(venc)}, "
            f"liquidity:{js_str(m['liquidity'])}, rating:{js_str(m['rating'])}}},"
        )
    if saldo:
        out.append(
            f"  {{id:'xp{len(posicoes)+1:02d}', src:'XP', ticker:'CONTA-XP', gics:'Financials', "
            f"product:'Conta Banco XP S.A. – saldo disponível', class:'RF',\n"
            f"   indexer:'CDI', applied:{saldo:.2f}, rent:0, rate:'', entryDate:'', "
            f"maturity:'Indeterminado', liquidity:'D+0', rating:''}},"
        )
    return out, novos


# ── Escrita no HTML ─────────────────────────────────────────────────────────
def atualizar_app(linhas_xp, data_ref):
    html = open(APP, encoding="utf-8").read()

    m = re.search(r"(const DEFAULT_PORTFOLIO = \[)([\s\S]*?)(\n\];)", html)
    if not m:
        sys.exit("ERRO: DEFAULT_PORTFOLIO não encontrado em " + APP)
    corpo = m.group(2)

    # remove entradas src:'XP' (bloco delimitado pelos marcadores)
    corpo = re.sub(r"\n *// ── XP[\s\S]*?// ── /XP ──+", "", corpo)
    # segurança: remove qualquer objeto solto com src:'XP'
    corpo = "\n".join(l for l in corpo.split("\n") if "src:'XP'" not in l and not re.match(r"^\s+indexer:.*applied:.*// xp$", l))

    bloco = ("\n  // ── XP — importado de PosicaoDetalhada.xlsx em " + data_ref +
             " (não editar à mão) ──\n" + "\n".join(linhas_xp) +
             "\n  // ── /XP ──────────────────────────────────────────────────")
    novo = m.group(1) + corpo.rstrip() + bloco + m.group(3)
    html = html[:m.start()] + novo + html[m.end():]

    # PORT_VERSION — força o navegador a recarregar a carteira
    mv = re.search(r"const PORT_VERSION = (\d+);", html)
    if mv:
        ver = int(mv.group(1)) + 1
        html = html.replace(mv.group(0), f"const PORT_VERSION = {ver};")
    else:
        ver = 1
        html = html.replace("const DEFAULT_PORTFOLIO = [",
                            f"const PORT_VERSION = {ver};\nconst DEFAULT_PORTFOLIO = [", 1)

    # newline="\n": no Windows o modo texto converteria tudo para CRLF e
    # quebraria os marcadores que o build/build.js procura.
    open(APP, "w", encoding="utf-8", newline="\n").write(html)
    return ver


def main():
    if len(sys.argv) > 1:
        src = sys.argv[1]
    else:
        cands = glob.glob(os.path.expanduser("~/Downloads/PosicaoDetalhada*.xlsx"))
        if not cands:
            sys.exit("Nenhum PosicaoDetalhada*.xlsx encontrado em Downloads. Passe o caminho como argumento.")
        src = max(cands, key=os.path.getmtime)
    print("Lendo:", src)

    rows = ler_xlsx(src)
    flat = " ".join(str(c) for r in rows[:3] for c in r)
    mdata = re.search(r"(\d{2}/\d{2}/\d{4})", flat)
    data_ref = mdata.group(1) if mdata else "?"

    saldo = None
    # linha de resumo do relatório: [patrimônio, total investido, saldo disponível, saldo projetado]
    for r in rows:
        vals = [money(c) for c in r if str(c).strip().startswith("R$")]
        if len(vals) >= 3 and vals[0] and vals[1] and abs(vals[0] - (vals[1] + (vals[2] or 0))) < 1:
            patrimonio, saldo = vals[0], vals[2]
            break
    else:
        patrimonio = None

    posicoes = parse_posicoes(rows)
    if not posicoes:
        sys.exit("ERRO: nenhuma posição reconhecida no arquivo.")

    linhas, novos = montar_bloco(posicoes, saldo, data_ref)
    total = sum(p["posicao"] for p in posicoes) + (saldo or 0)

    print(f"\nData de referência: {data_ref}")
    print(f"{len(posicoes)} posições + saldo disponível\n")
    for p in posicoes:
        rent = (p["posicao"] / p["aplicado"] - 1) * 100 if p["aplicado"] else 0
        print(f"  {p['nome'][:44]:<44} R$ {p['posicao']:>10,.2f}  ({rent:+6.2f}%)")
    if saldo:
        print(f"  {'Saldo disponível':<44} R$ {saldo:>10,.2f}")
    print(f"  {'':<44} {'-'*14}")
    print(f"  {'TOTAL XP':<44} R$ {total:>10,.2f}")
    if patrimonio and abs(total - patrimonio) > 0.05:
        print(f"  ⚠ Relatório informa R$ {patrimonio:,.2f} — diferença de R$ {total-patrimonio:,.2f}")
    elif patrimonio:
        print(f"  ✓ Confere com o total do relatório")

    if novos:
        print("\n⚠ Produtos novos (classificados como Diversificado/RF por padrão):")
        for n in novos:
            print("   -", n)
        print("   Adicione-os ao CATALOGO em importar_xp.py para classificar melhor.")

    ver = atualizar_app(linhas, data_ref)
    print(f"\n✓ caixa-v9-local-backup.html atualizado (PORT_VERSION = {ver})")
    print("  Próximo passo:  py publicar.py")


if __name__ == "__main__":
    main()
