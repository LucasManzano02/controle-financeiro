# -*- coding: utf-8 -*-
"""
update_prices.py — Atualizador de preços da carteira (caixa-v9.html)

Estratégia de eficiência (mínimo de requisições):
  1. Cache: se caixa-prices.json tem menos de CACHE_HOURS, não faz nenhuma requisição.
  2. Tesouro Direto: UMA requisição pública da B3 cobre todos os títulos (LFT, NTN-B...).
  3. Yahoo Finance: UMA requisição por ticker via endpoint chart (sem lib externa, sem auth).
     USD/BRL é buscado uma única vez e reaproveitado para converter ativos em dólar.
  4. ANBIMA (fundos por CNPJ): usado somente se houver credenciais no ambiente
     (ANBIMA_CLIENT_ID / ANBIMA_CLIENT_SECRET). Sem credenciais, os fundos são
     pulados silenciosamente — no app eles continuam avaliados por rentabilidade %.

Saída: caixa-prices.json  →  importar no app via botão "↑ Preços" na aba Carteira.
Uso:   python update_prices.py            (respeita cache)
       python update_prices.py --force    (ignora cache)

Dependências: apenas stdlib (urllib). Nenhum pip install necessário.
"""
import json, os, sys, time, base64
import urllib.request

# Console Windows usa cp1252 — força UTF-8 para não quebrar nos acentos/símbolos
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_FILE = os.path.join(BASE, "caixa-prices.json")
CACHE_HOURS = 24

# ── Configuração dos ativos ──────────────────────────────────────────────────
# source: 'tesouro' (tipo+vencimento) | 'yahoo' (symbol) | 'anbima' (CNPJ)
# NOTA: as posições da XP (fundos, CRI/CRA/CDB) vêm prontas do extrato mensal
# via importar_xp.py — não precisam ser buscadas aqui. Este script cobre apenas
# o que a XP não reporta: os ativos no exterior (Avenue).
ASSETS = {
    "SPY": {"source": "yahoo", "symbol": "SPY", "currency": "USD"},
}

UA = {"User-Agent": "Mozilla/5.0 (carteira-pessoal; +local)"}


def http_json(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def cache_fresh():
    if not os.path.exists(OUT_FILE):
        return False
    try:
        with open(OUT_FILE, encoding="utf-8") as f:
            data = json.load(f)
        age_h = (time.time() - data.get("updated_ts", 0)) / 3600
        return age_h < CACHE_HOURS
    except Exception:
        return False


# ── Fontes ───────────────────────────────────────────────────────────────────
def fetch_tesouro():
    """Uma requisição cobre todos os títulos do Tesouro Direto.

    Usa o dataset aberto oficial do Tesouro Transparente (CSV histórico completo,
    ~14MB). O antigo endpoint JSON da B3 (tesourodireto.com.br) foi descontinuado
    (HTTP 410). Para não carregar o arquivo inteiro em memória, processa linha a
    linha e mantém apenas os títulos configurados em ASSETS, ficando com a
    'Data Base' mais recente de cada um (PU Venda Manhã = preço de recompra,
    que é o valor de marcação a mercado correto para o investidor).
    """
    url = ("https://www.tesourotransparente.gov.br/ckan/dataset/"
           "df56aa42-484a-4a59-8184-7676580c81e3/resource/"
           "796d2059-14e9-44e3-80c9-2d9e30b405c1/download/PrecoTaxaTesouroDireto.csv")

    wanted = {(c["tipo"], c["vencimento"]): ticker
              for ticker, c in ASSETS.items() if c["source"] == "tesouro"}
    if not wanted:
        return {}

    best = {}  # (tipo,vencimento) -> (data_base_ordinal, pu_venda)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        next(r, None)  # pula o header
        for raw in r:
            parts = raw.decode("utf-8", "ignore").strip().split(";")
            if len(parts) < 7:
                continue
            key = (parts[0], parts[1])
            if key not in wanted:
                continue
            try:
                d, m, y = parts[2].split("/")
                data_ord = int(y) * 10000 + int(m) * 100 + int(d)
                pu_venda = float(parts[6].replace(",", "."))
            except (ValueError, IndexError):
                continue
            if key not in best or data_ord > best[key][0]:
                best[key] = (data_ord, pu_venda)

    return {wanted[k]: v[1] for k, v in best.items()}


def fetch_yahoo(symbol):
    """Endpoint chart público — 1 requisição, sem autenticação."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
    data = http_json(url)
    return float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])


def fetch_anbima_token():
    cid = os.environ.get("ANBIMA_CLIENT_ID")
    sec = os.environ.get("ANBIMA_CLIENT_SECRET")
    if not (cid and sec):
        return None
    auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    req = urllib.request.Request(
        "https://api.anbima.com.br/oauth/access-token",
        data=json.dumps({"grant_type": "client_credentials"}).encode(),
        headers={**UA, "Content-Type": "application/json", "Authorization": f"Basic {auth}"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["access_token"]


def fetch_anbima_fundo(cnpj, token, cid):
    """Cota mais recente do fundo via API ANBIMA (dados de fundos)."""
    clean = "".join(c for c in cnpj if c.isdigit())
    url = f"https://api.anbima.com.br/feed/fundos/v1/fundos/{clean}/serie-historica"
    data = http_json(url, headers={"client_id": cid, "access_token": token})
    serie = data if isinstance(data, list) else data.get("content", [])
    if serie:
        last = serie[-1]
        return float(last.get("valor_cota") or 0)
    return 0


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    force = "--force" in sys.argv
    if not force and cache_fresh():
        print(f"Cache válido (<{CACHE_HOURS}h) — nenhuma requisição feita. Use --force para ignorar.")
        return

    prices, errors = {}, []

    # 1) Tesouro: 1 request para todos
    tesouro_assets = {t: c for t, c in ASSETS.items() if c["source"] == "tesouro"}
    if tesouro_assets:
        try:
            tbl = fetch_tesouro()
            for ticker, cfg in tesouro_assets.items():
                p = tbl.get(ticker)
                if p: prices[ticker] = round(p, 2)
                else: errors.append(f"{ticker}: '{cfg['tipo']} {cfg['vencimento']}' não encontrado na tabela do Tesouro")
        except Exception as e:
            errors.append(f"Tesouro Direto: {e}")

    # 2) USD/BRL: 1 request, reutilizado
    usdbrl = None
    needs_usd = any(c.get("currency") == "USD" for c in ASSETS.values())
    if needs_usd:
        try:
            usdbrl = fetch_yahoo("USDBRL=X")
            prices["USDBRL"] = round(usdbrl, 4)
        except Exception as e:
            errors.append(f"USDBRL: {e}")

    # 3) Yahoo por ticker
    for ticker, cfg in ASSETS.items():
        if cfg["source"] != "yahoo":
            continue
        try:
            p = fetch_yahoo(cfg["symbol"])
            if cfg.get("currency") == "USD" and usdbrl:
                p *= usdbrl                      # converte para BRL
            prices[ticker] = round(p, 2)
        except Exception as e:
            errors.append(f"{ticker}: {e}")

    # 4) ANBIMA (opcional — requer credenciais)
    anbima_assets = [t for t, c in ASSETS.items() if c["source"] == "anbima"]
    if anbima_assets:
        try:
            token = fetch_anbima_token()
            if token:
                cid = os.environ["ANBIMA_CLIENT_ID"]
                for cnpj in anbima_assets:
                    try:
                        p = fetch_anbima_fundo(cnpj, token, cid)
                        if p: prices[cnpj] = round(p, 6)
                    except Exception as e:
                        errors.append(f"{cnpj}: {e}")
            else:
                print(f"ANBIMA: sem credenciais — {len(anbima_assets)} fundos pulados (avaliados por rent% no app).")
        except Exception as e:
            errors.append(f"ANBIMA auth: {e}")

    out = {
        "updated_ts": time.time(),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "prices": prices,
        "errors": errors,
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"✓ {len(prices)} preços gravados em {os.path.basename(OUT_FILE)}")
    for e in errors:
        print(f"  ⚠ {e}")
    print("Importe no app: aba Carteira → ↑ Preços → selecione caixa-prices.json")


if __name__ == "__main__":
    main()
