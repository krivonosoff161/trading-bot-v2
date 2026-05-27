# -*- coding: utf-8 -*-
"""
sentiment_edge_peek.py — ДЕШЁВЫЙ зонд: есть ли намёк на эдж в OKX news-sentiment на тех ~30 днях,
что отдаёт API (глубокого бэктеста нет — данные shallow). Тянет sentiment-trend через офиц. OKX MCP
(read-only), кладёт рядом с дневными ценами из _okxhist, считает IC(net_sentiment[t] → forward-ход[t+1]).
Tiny/noisy n — НЕ доказательство, а решение «стоит ли форвард-сбор». Read-only.

ПРЕДСКАЗАНИЕ: IC≈0 или слабо-ОТРИЦАТЕЛЬНЫЙ (контртренд — пик булиша = локальная вершина). Робастного + не жду.
"""
import sys, io, os, csv, json, subprocess, threading, time
import numpy as np
from scipy.stats import spearmanr
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_okxhist")
CMD = r"C:\Users\krivo\AppData\Roaming\npm\okx-trade-mcp.cmd"
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "LTC", "DOT", "TRX", "SUI", "APT", "NEAR"]

class MCP:
    def __init__(self):
        self.p = subprocess.Popen(["cmd", "/c", CMD, "--modules", "market,news", "--read-only"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, encoding="utf-8", bufsize=1)
        self.lines = []; self._id = 0
        threading.Thread(target=lambda: [self.lines.append(l.strip()) for l in self.p.stdout], daemon=True).start()
    def _send(self, o): self.p.stdin.write(json.dumps(o) + "\n"); self.p.stdin.flush()
    def call(self, method, params=None, notify=False):
        if notify: self._send({"jsonrpc": "2.0", "method": method, "params": params or {}}); return
        self._id += 1; rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        for _ in range(100):
            for ln in self.lines:
                try: o = json.loads(ln)
                except Exception: continue
                if o.get("id") == rid: return o
            time.sleep(0.1)
        return None
    def close(self):
        try: self.p.terminate()
        except Exception: pass

def daily_close(sym):
    p = os.path.join(HDIR, f"{sym}_1D.csv")
    if not os.path.exists(p): return {}
    out = {}
    with open(p, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[int(r["ts"]) // 86400000] = float(r["c"])   # ключ = день (epoch-дни)
    return out

def main():
    m = MCP()
    m.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "p", "version": "1"}})
    m.call("notifications/initialized", notify=True)
    S = []; F = []   # net_sentiment, forward 1d return
    perpts = 0
    for c in COINS:
        r = m.call("tools/call", {"name": "news_get_coin_sentiment",
                                  "arguments": {"coins": c, "period": "24h", "trendPoints": 30}})
        try:
            txt = r["result"]["content"][0]["text"]; obj = json.loads(txt)
            trend = obj["data"]["data"][0]["details"][0]["trend"]
        except Exception:
            continue
        px = daily_close(c)
        rows = []
        for pt in trend:
            day = int(pt["ts"]) // 86400000
            net = float(pt["bullishRatio"]) - float(pt["bearishRatio"])
            rows.append((day, net))
        rows.sort()
        perpts = max(perpts, len(rows))
        for i in range(len(rows) - 1):
            d0, net = rows[i]; d1 = rows[i + 1][0]
            if d0 in px and d1 in px and px[d0]:
                S.append(net); F.append((px[d1] - px[d0]) / px[d0] * 100)
    m.close()
    n = len(S)
    print(f"=== SENTIMENT-EDGE ЗОНД: {n} точек ({len(COINS)} монет × ~{perpts} дн trend) ===\n")
    if n < 30:
        print("мало данных (trend shallow / нет выравнивания с ценой)"); return
    S = np.array(S); F = np.array(F)
    r = spearmanr(S, F)[0]; t = r * np.sqrt((n - 2) / (1 - r**2 + 1e-12))
    print(f"IC(net_sentiment[t] → forward 1d ход): {r:+.3f}  t={t:+.1f}  {'ЗНАЧИМ' if abs(t) > 2 else 'шум'} (n={n})")
    # хвосты: экстремальный булиш/бэриш
    hi = S > np.percentile(S, 75); loq = S < np.percentile(S, 25)
    print(f"  верх-квартиль булиша: ср.ход вперёд {F[hi].mean():+.2f}%  | низ-квартиль: {F[loq].mean():+.2f}%")
    print(f"\nРЕШЕНИЕ: если IC≈0/незначим на этих 30д — форвард-сбор вряд ли окупится; если намёк есть — ставим сбор и копим чисто.")

if __name__ == "__main__":
    main()
