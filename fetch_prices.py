#!/usr/bin/env python3
"""Napi kripto OHLCV letöltő (csak Python standard könyvtár).

Coinonként sorban próbálja a forrásokat (Bybit -> Binance -> OKX -> Kraken),
és az első működő adatait összefésüli a data/ohlcv.csv fájllal.
A még le nem zárt (mai, UTC) gyertyát kihagyja.

2. rész (best effort, hibatűrő): piaci kiegészítők a data/ mappába:
  market.csv       napi pillanatkép: Fear & Greed, BTC-dominancia, teljes kapitalizáció,
                   BTC/ETH/SOL funding (8 órás) és open interest, US 2Y/10Y, DXY (EKB-árfolyamokból számolva), EUR/USD
  fng.csv          Fear & Greed teljes története
  stablecoins.csv  stablecoin-kínálat teljes története (DefiLlama)
  treasury.csv     amerikai állampapírhozamok (idei év, napi)
Hogy melyik forrás működött, az a data/status.json "extras" mezőjében látszik.
"""
import csv, json, os, sys, time, urllib.request
from datetime import datetime, timezone

COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "LINK", "AVAX", "LTC", "TRX", "DOT"]
KRAKEN = {"BTC": "XBTUSD", "DOGE": "XDGUSD"}
OUT = os.path.join("data", "ohlcv.csv")
FIELDS = ["date", "coin", "open", "high", "low", "close", "volume", "source"]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (price-fetcher)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def get_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (price-fetcher)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"User-Agent": "Mozilla/5.0 (price-fetcher)", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def day(ts_ms):
    return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def parse_bybit(j):
    if j.get("retCode") != 0:
        raise ValueError(j.get("retMsg"))
    return [(day(r[0]), r[1], r[2], r[3], r[4], r[5]) for r in j["result"]["list"]]


def parse_binance(j):
    return [(day(r[0]), r[1], r[2], r[3], r[4], r[5]) for r in j]


def parse_okx(j):
    if j.get("code") != "0":
        raise ValueError(j.get("msg"))
    return [(day(r[0]), r[1], r[2], r[3], r[4], r[5]) for r in j["data"]]


def parse_kraken(j):
    if j.get("error"):
        raise ValueError(j["error"])
    rows = [v for k, v in j["result"].items() if k != "last"][0]
    return [(day(r[0] * 1000), r[1], r[2], r[3], r[4], r[6]) for r in rows]


def sources(c):
    return [
        ("bybit", f"https://api.bybit.com/v5/market/kline?category=spot&symbol={c}USDT&interval=D&limit=1000", parse_bybit),
        ("binance", f"https://data-api.binance.vision/api/v3/klines?symbol={c}USDT&interval=1d&limit=1000", parse_binance),
        ("okx", f"https://www.okx.com/api/v5/market/history-candles?instId={c}-USDT&bar=1Dutc&limit=100", parse_okx),
        ("kraken", f"https://api.kraken.com/0/public/OHLC?pair={KRAKEN.get(c, c + 'USD')}&interval=1440", parse_kraken),
    ]


def fetch(c):
    for name, url, parse in sources(c):
        try:
            rows = parse(get(url))
            if len(rows) >= 5:
                print(f"{c}: {name} OK ({len(rows)} nap)")
                return name, rows
            print(f"{c}: {name} túl kevés adat")
        except Exception as e:  # következő forrás
            print(f"{c}: {name} hiba: {str(e)[:120]}")
        time.sleep(0.5)
    return None, []


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = {}
    if os.path.exists(OUT):
        with open(OUT, newline="") as f:
            for r in csv.DictReader(f):
                data[(r["date"], r["coin"])] = r
    failed = []
    for c in COINS:
        src, rows = fetch(c)
        if not rows:
            failed.append(c)
            continue
        for d, o, h, l, cl, v in rows:
            if d >= today:
                continue  # nem lezárt gyertya
            data[(d, c)] = dict(date=d, coin=c, open=o, high=h, low=l, close=cl, volume=v, source=src)
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for k in sorted(data):
            w.writerow(data[k])
    last = max((k[0] for k in data), default="-")
    try:
        ex = extras(today)
    except Exception as e:  # a kiegészítők hibája nem állíthatja meg az árfolyamokat
        ex = {"extras": "hiba: " + str(e)[:100]}
    with open(os.path.join("data", "status.json"), "w") as f:
        json.dump({"updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "last_date": last, "rows": len(data), "failed": failed, "extras": ex}, f)
    print(f"Kész: {len(data)} sor, utolsó nap {last}, sikertelen: {failed or 'nincs'}")
    if len(failed) == len(COINS):
        sys.exit(1)


# ---------------------------------------------------------------- 2. rész: kiegészítők
def write_csv(name, fields, rows):
    os.makedirs("data", exist_ok=True)
    with open(os.path.join("data", name), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def read_csv(name):
    p = os.path.join("data", name)
    if not os.path.exists(p):
        return []
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


def parse_fng(j):
    return sorted(({"date": day(int(r["timestamp"]) * 1000), "value": r["value"], "label": r["value_classification"]}
                   for r in j["data"]), key=lambda r: r["date"])


def parse_global_cg(j):
    d = j["data"]
    return {"btc_dom": round(d["market_cap_percentage"]["btc"], 2), "total_mcap_usd": round(d["total_market_cap"]["usd"]), "global_src": "coingecko"}


def parse_global_cp(j):
    return {"btc_dom": round(j["bitcoin_dominance_percentage"], 2), "total_mcap_usd": round(j["market_cap_usd"]), "global_src": "coinpaprika"}


def parse_stables(j):
    out = []
    for r in j:
        v = (r.get("totalCirculatingUSD") or r.get("totalCirculating") or {}).get("peggedUSD")
        if v:
            out.append({"date": day(int(r["date"]) * 1000), "total_usd": round(v)})
    return out


def parse_treasury(text):
    out = []
    for r in csv.DictReader(text.splitlines()):
        m, d, y = r["Date"].split("/")
        out.append({"date": f"{y}-{m.zfill(2)}-{d.zfill(2)}", "us2y": r.get("2 Yr", ""), "us10y": r.get("10 Yr", ""), "us30y": r.get("30 Yr", "")})
    return sorted(out, key=lambda r: r["date"])


def parse_stooq(text):
    rows = list(csv.DictReader(text.splitlines()))
    last = rows[-1]
    return last["Date"], float(last["Close"])


def deriv_okx(c):
    f = get(f"https://www.okx.com/api/v5/public/funding-rate?instId={c}-USDT-SWAP")["data"][0]
    o = get(f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={c}-USDT-SWAP")["data"][0]
    oi = o.get("oiUsd")
    return float(f["fundingRate"]), (round(float(oi)) if oi else ""), "okx"


def parse_hl(j, c):
    names = [u["name"] for u in j[0]["universe"]]
    x = j[1][names.index(c)]
    return float(x["funding"]) * 8, round(float(x["openInterest"]) * float(x["markPx"])), "hyperliquid"


def deriv_hl(c):
    return parse_hl(post("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"}), c)


def deriv_deribit(c):
    inst = {"BTC": "BTC-PERPETUAL", "ETH": "ETH-PERPETUAL", "SOL": "SOL_USDC-PERPETUAL"}[c]
    r = get(f"https://www.deribit.com/api/v2/public/ticker?instrument_name={inst}")["result"]
    oi = r["open_interest"] if c != "SOL" else r["open_interest"] * r["mark_price"]
    return float(r["funding_8h"]), round(oi), "deribit"


def extras(today):
    st, row = {}, {"date": today}

    def step(name, fn):
        try:
            fn()
            st[name] = "ok"
        except Exception as e:
            st[name] = "hiba: " + str(e)[:100]
        print(f"{name}: {st[name]}")

    def fng():
        rows = parse_fng(get("https://api.alternative.me/fng/?limit=0"))
        write_csv("fng.csv", ["date", "value", "label"], rows)
        row["fng"], row["fng_date"] = rows[-1]["value"], rows[-1]["date"]

    def glob():
        try:
            row.update(parse_global_cg(get("https://api.coingecko.com/api/v3/global")))
        except Exception:
            row.update(parse_global_cp(get("https://api.coinpaprika.com/v1/global")))

    def stables():
        rows = parse_stables(get("https://stablecoins.llama.fi/stablecoincharts/all"))
        write_csv("stablecoins.csv", ["date", "total_usd"], rows)
        row["stables_usd"] = rows[-1]["total_usd"]

    def treasury():
        y = today[:4]
        t = get_text("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
                     f"{y}/all?type=daily_treasury_yield_curve&field_tdr_date_value={y}&page&_format=csv")
        rows = parse_treasury(t)
        old = {r["date"]: r for r in read_csv("treasury.csv")}
        old.update({r["date"]: r for r in rows})
        write_csv("treasury.csv", ["date", "us2y", "us10y", "us30y"], [old[k] for k in sorted(old)])
        row["us2y"], row["us10y"], row["ust_date"] = rows[-1]["us2y"], rows[-1]["us10y"], rows[-1]["date"]

    def fx():
        # EKB napi referencia-árfolyamok (frankfurter.app); a DXY a hivatalos képlettel számolva
        j = get("https://api.frankfurter.app/latest?from=USD&to=EUR,JPY,GBP,CAD,SEK,CHF")
        r = j["rates"]
        row["eurusd"], row["eurusd_date"] = round(1 / r["EUR"], 4), j["date"]
        dxy = (50.14348112 * (1 / r["EUR"]) ** -0.576 * r["JPY"] ** 0.136 * (1 / r["GBP"]) ** -0.119
               * r["CAD"] ** 0.091 * r["SEK"] ** 0.042 * r["CHF"] ** 0.036)
        row["dxy"], row["dxy_date"] = round(dxy, 2), j["date"]

    def make_deriv(c):
        def f():
            errs = []
            for fn in (deriv_okx, deriv_hl, deriv_deribit):
                try:
                    fr, oi, src = fn(c)
                    row[f"{c.lower()}_funding_8h"], row[f"{c.lower()}_oi_usd"], row[f"{c.lower()}_deriv_src"] = fr, oi, src
                    return
                except Exception as e:
                    errs.append(f"{fn.__name__}: {str(e)[:40]}")
            raise ValueError("; ".join(errs))
        return f

    step("fng", fng)
    step("global", glob)
    step("stablecoins", stables)
    step("treasury", treasury)
    step("fx", fx)
    for c in ("BTC", "ETH", "SOL"):
        step("deriv_" + c, make_deriv(c))

    fields = ["date", "fng", "fng_date", "btc_dom", "total_mcap_usd", "global_src", "stables_usd",
              "btc_funding_8h", "btc_oi_usd", "btc_deriv_src", "eth_funding_8h", "eth_oi_usd", "eth_deriv_src",
              "sol_funding_8h", "sol_oi_usd", "sol_deriv_src", "us2y", "us10y", "ust_date",
              "dxy", "dxy_date", "eurusd", "eurusd_date"]
    old = {r["date"]: r for r in read_csv("market.csv")}
    old[today] = row
    write_csv("market.csv", fields, [old[k] for k in sorted(old)])
    return st


if __name__ == "__main__":
    main()
