#!/usr/bin/env python3
"""Napi kripto OHLCV letöltő (csak Python standard könyvtár).

Coinonként sorban próbálja a forrásokat (Bybit -> Binance -> OKX -> Kraken),
és az első működő adatait összefésüli a data/ohlcv.csv fájllal.
A még le nem zárt (mai, UTC) gyertyát kihagyja.
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
    with open(os.path.join("data", "status.json"), "w") as f:
        json.dump({"updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "last_date": last, "rows": len(data), "failed": failed}, f)
    print(f"Kész: {len(data)} sor, utolsó nap {last}, sikertelen: {failed or 'nincs'}")
    if len(failed) == len(COINS):
        sys.exit(1)


if __name__ == "__main__":
    main()
