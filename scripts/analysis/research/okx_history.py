# -*- coding: utf-8 -*-
"""
okx_history.py — тянет длинную историю свечей с OKX (history-candles, пагинация) и кэширует в CSV.
Для Branch A (SFP на мажорах) и Branch B (доминации/ETHBTC/моментум на дневках). Read-only (только GET).
Usage: python okx_history.py <bar> <days> <SYM1> <SYM2> ...   напр: python okx_history.py 1D 500 BTC ETH SOL
"""
import sys, io, os, json, time, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_okxhist")
os.makedirs(OUT, exist_ok=True)
BARMS = {"5m": 300000, "15m": 900000, "1H": 3600000, "4H": 14400000, "1D": 86400000}

def get(url):
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(0.5)
    return {}

def fetch(inst, bar, days):
    need = int(days * 86400000 / BARMS[bar])
    rows = {}; after = ""
    while len(rows) < need:
        url = f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar={bar}&limit=100"
        if after: url += f"&after={after}"
        j = get(url); d = j.get("data") or []
        if not d: break
        for r in d:
            rows[int(r[0])] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
        after = d[-1][0]                 # самый старый ts в пачке → следующая пачка ещё старше
        time.sleep(0.12)
        if len(d) < 100: break
    return dict(sorted(rows.items()))

def main():
    bar = sys.argv[1]; days = int(sys.argv[2]); syms = sys.argv[3:]
    for s in syms:
        inst = f"{s}-USDT-SWAP"
        m = fetch(inst, bar, days)
        p = os.path.join(OUT, f"{s}_{bar}.csv")
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write("ts,o,h,l,c,v\n")
            for t, (o, h, l, c, v) in m.items():
                fh.write(f"{t},{o},{h},{l},{c},{v}\n")
        rng = ""
        if m:
            ks = list(m)
            from datetime import datetime
            rng = f"{datetime.utcfromtimestamp(ks[0]/1000):%Y-%m-%d}..{datetime.utcfromtimestamp(ks[-1]/1000):%Y-%m-%d}"
        print(f"  {s:6} {bar}: {len(m):5} баров  {rng}  → {os.path.basename(p)}")

if __name__ == "__main__":
    main()
