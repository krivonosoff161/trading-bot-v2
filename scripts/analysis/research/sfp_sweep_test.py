# -*- coding: utf-8 -*-
"""
sfp_sweep_test.py — Branch A: единственная механически-тестируемая ICT-идея (3 канала, ссылка Tom Dante).
SFP / liquidity sweep: цена прокалывает фитилём прежний свинг-хай/лой (кластер стопов) и закрывается
обратно ВНУТРЬ → фейд в сторону разворота. Read-only, на 5m фичах (11-26 май), train/holdout, честные косты.

ЗАФИКСИРОВАННОЕ ПРЕДСКАЗАНИЕ (до запуска, против граблей эйфории):
  Аутопсия MR показала: на сильных экстремумах ход ПРОДОЛЖАЕТСЯ (fade gross-минус), MR-эдж ниже косты.
  → Предсказываю: SFP на ТОНКИХ альтах — NET минус после косты и в train, и в holdout.
    На МАЖОРАХ (BTC/ETH/ликвид) возможен слабый gross-плюс (стоп-ханты реальны), но косты съедают → net~0/слегка минус.
    Робастного OOS-эджа НЕ ожидаю. Если holdout-мажоры net>0 устойчиво — вот это сюрприз, его и копать.
"""
import sys, io, os, gzip, csv
from datetime import datetime, timedelta
import numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
F5 = os.path.join(ROOT, "logs", "features", "5m")
if not os.path.isdir(F5) or not os.listdir(F5):          # после сброса 27.05 фичи переехали в архив
    F5 = os.path.join(ROOT, "logs_archive", "pre-reset_2026-05-27", "features", "5m")
TRAIN_END = "2026-05-18"
MAJORS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "LTC", "DOT", "TRX", "SUI", "APT"}
W = 12          # свинг-окно (1ч на 5m) для прежнего хая/лоя
H = 6           # макс холд, баров
RR = 2.0        # тейк = RR * риск (риск = от входа до стопа за фитилём)

def fnum(x):
    try: return float(x)
    except: return None

def load(sym):
    base = os.path.join(F5, sym); rows = []
    if not os.path.isdir(base): return []
    d = datetime(2026, 5, 11); e = datetime(2026, 5, 26)
    while d <= e:
        for ext, op in ((".csv.gz", gzip.open), (".csv", open)):
            p = os.path.join(base, d.strftime("%Y-%m-%d") + ext)
            if os.path.exists(p):
                with op(p, "rt", encoding="utf-8", errors="ignore") as fh:
                    rows += list(csv.DictReader(fh)); break
        d += timedelta(days=1)
    seen = set(); out = []
    for r in sorted(rows, key=lambda r: int(r["ts_ms"])):
        if r["ts_ms"] not in seen: seen.add(r["ts_ms"]); out.append(r)
    return out

def trades_for(sym):
    rows = load(sym)
    if len(rows) < W + H + 5: return []
    c = [fnum(r["close"]) for r in rows]; hi = [fnum(r["high"]) for r in rows]; lo = [fnum(r["low"]) for r in rows]
    ts = [int(r["ts_ms"]) for r in rows]
    out = []
    for i in range(W, len(rows) - H - 1):
        if None in (c[i], hi[i], lo[i]): continue
        win_hi = [hi[j] for j in range(i - W, i) if hi[j] is not None]
        win_lo = [lo[j] for j in range(i - W, i) if lo[j] is not None]
        if len(win_hi) < W or len(win_lo) < W: continue
        sh = max(win_hi); sl = min(win_lo)
        day = datetime.utcfromtimestamp(ts[i] / 1000).strftime("%Y-%m-%d")
        side = 0
        if hi[i] > sh and c[i] < sh: side = -1          # sweep high → bearish SFP
        elif lo[i] < sl and c[i] > sl: side = 1         # sweep low → bullish SFP
        if side == 0: continue
        entry = c[i]
        stop = hi[i] if side < 0 else lo[i]             # за фитилём прокола
        risk = abs(stop - entry)
        if risk <= 0 or entry <= 0: continue
        target = entry - side * (-RR * risk) if False else entry + side * (RR * risk)
        # проход вперёд: что раньше — стоп или тейк
        res = None
        for j in range(i + 1, min(i + 1 + H, len(rows))):
            if None in (hi[j], lo[j]): continue
            if side < 0:
                if hi[j] >= stop: res = -risk; break
                if lo[j] <= target: res = RR * risk; break
            else:
                if lo[j] <= stop: res = -risk; break
                if hi[j] >= target: res = RR * risk; break
        if res is None:                                  # таймаут — закрытие по последнему close
            jx = min(i + H, len(rows) - 1)
            res = side * (c[jx] - entry) if c[jx] else 0.0
        ret_pct = side * 0 + (res / entry) * 100         # P&L в % от входа
        out.append((day, ret_pct))
    return out

def report(name, tr, cost_pct):
    if not tr:
        print(f"  {name:18} нет сделок"); return
    g = np.array([r for _, r in tr])
    net = g - cost_pct                                   # косты на круг (вход+выход)
    wr = 100 * np.mean(net > 0)
    print(f"  {name:18} n={len(g):5}  gross={g.mean():+.3f}%  net={net.mean():+.3f}%  WR={wr:4.0f}%  cost/rt={cost_pct:.2f}%")

def main():
    syms = sorted(os.listdir(F5))
    maj_tr, maj_ho, alt_tr, alt_ho = [], [], [], []
    for s in syms:
        tr = trades_for(s)
        if not tr: continue
        is_major = s in MAJORS
        for day, r in tr:
            holdout = day > TRAIN_END
            bucket = (maj_ho if holdout else maj_tr) if is_major else (alt_ho if holdout else alt_tr)
            bucket.append((day, r))
    print(f"=== SFP / LIQUIDITY-SWEEP (W={W} H={H} RR={RR}) — фейд прокола свинг-уровня ===\n")
    print("МАЖОРЫ (ликвид, кост ~0.04%/круг тейкер):")
    report("major TRAIN", maj_tr, 0.04); report("major HOLDOUT", maj_ho, 0.04)
    print("\nТОНКИЕ АЛЬТЫ (кост ~0.12%/круг реалистично):")
    report("alt TRAIN", alt_tr, 0.12); report("alt HOLDOUT", alt_ho, 0.12)
    print("\nКонтроль — мажоры на мейкер-косте 0.02%:")
    report("major HOLDOUT mk", maj_ho, 0.02)
    print("\nВЕРДИКТ: эдж живой ТОЛЬКО если net>0 устойчиво в HOLDOUT (особенно мажоры). Иначе SFP — миф для нас.")

if __name__ == "__main__":
    main()
