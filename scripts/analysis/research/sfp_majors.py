# -*- coding: utf-8 -*-
"""
sfp_majors.py — Branch A, добивка: SFP/liquidity-sweep на ЛИКВИДНЫХ МАЖОРАХ (15m из OKX-истории, 90д).
Открытый вопрос: на мажорах стоп-ханты реальны, кост низкий — работает ли там фейд прокола свинг-уровня?
train/holdout по дате. Read-only.

ПРЕДСКАЗАНИЕ: на мажорах gross может быть слегка плюсовым (стоп-ханты есть), но робастного OOS-net
после даже низкой косты НЕ жду — иначе это давно был бы известный механический грааль.
"""
import sys, io, os, csv
from datetime import datetime
import numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
H = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_okxhist")
MAJORS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK"]
W = 20           # свинг-окно (5ч на 15m)
HOLD = 8         # макс холд баров (2ч)
RR = 2.0

def load(sym):
    p = os.path.join(H, f"{sym}_15m.csv")
    if not os.path.exists(p): return None
    ts, o, hi, lo, c = [], [], [], [], []
    with open(p, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            ts.append(int(r["ts"])); o.append(float(r["o"])); hi.append(float(r["h"]))
            lo.append(float(r["l"])); c.append(float(r["c"]))
    return ts, np.array(o), np.array(hi), np.array(lo), np.array(c)

def trades(sym):
    d = load(sym)
    if not d: return []
    ts, o, hi, lo, c = d
    n = len(c); out = []
    for i in range(W, n - HOLD - 1):
        sh = hi[i - W:i].max(); sl = lo[i - W:i].min()
        side = 0
        if hi[i] > sh and c[i] < sh: side = -1
        elif lo[i] < sl and c[i] > sl: side = 1
        if side == 0: continue
        entry = c[i]; stop = hi[i] if side < 0 else lo[i]
        risk = abs(stop - entry)
        if risk <= 0: continue
        target = entry + side * RR * risk
        res = None
        for j in range(i + 1, i + 1 + HOLD):
            if side < 0:
                if hi[j] >= stop: res = -risk; break
                if lo[j] <= target: res = RR * risk; break
            else:
                if lo[j] <= stop: res = -risk; break
                if hi[j] >= target: res = RR * risk; break
        if res is None:
            res = side * (c[i + HOLD] - entry)
        day = datetime.utcfromtimestamp(ts[i] / 1000).strftime("%Y-%m-%d")
        out.append((day, res / entry * 100))
    return out

def rep(name, tr, cost):
    if not tr: print(f"  {name:16} нет сделок"); return
    g = np.array([r for _, r in tr]); net = g - cost
    print(f"  {name:16} n={len(g):5} gross={g.mean():+.3f}% net={net.mean():+.3f}% WR={100*np.mean(net>0):4.0f}% (cost {cost:.2f}%)")

def main():
    allt = []
    for s in MAJORS:
        tr = trades(s)
        if tr: allt += [(s, d, r) for d, r in tr]
    if not allt:
        print("нет данных 15m мажоров (фетч не завершён?)"); return
    days = sorted({d for _, d, _ in allt}); split = days[int(len(days) * 0.6)]
    print(f"=== SFP МАЖОРЫ 15m (W={W} HOLD={HOLD} RR={RR}) — {len(MAJORS)} монет ===")
    print(f"train < {split} <= holdout\n")
    tr_t = [(d, r) for _, d, r in allt if d < split]
    tr_h = [(d, r) for _, d, r in allt if d >= split]
    print("Все мажоры (тейкер 0.04% / мейкер 0.02% на круг):")
    rep("TRAIN tk", tr_t, 0.04); rep("HOLDOUT tk", tr_h, 0.04); rep("HOLDOUT mk", tr_h, 0.02)
    print("\nПо монетам (holdout, тейкер 0.04%):")
    for s in MAJORS:
        rep(s, [(d, r) for sm, d, r in allt if sm == s and d >= split], 0.04)
    print("\nВЕРДИКТ: SFP жив на мажорах ТОЛЬКО при устойчивом net>0 в HOLDOUT. Иначе закрыто окончательно.")

if __name__ == "__main__":
    main()
