"""
geometry_sweep.py - parameter sweep of the V3 exit (ATR stop + trail [+ partial]).

Grid over stop_k x trail_k, optionally taking half off at +1R. Trades' tick
paths are cached once, then every combo is replayed in memory. Anti-overfit:
time-split train/holdout (main_ws) + look for a robust PLATEAU, not a lone peak.
Read-only. Usage: python scripts/analysis/research/geometry_sweep.py
"""
from __future__ import annotations

import sys
import io

if not getattr(sys, "_utf8_wrapped", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._utf8_wrapped = True

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from geometry_model import load_ticks, load_trades, OUT
from geometry_cases import resample
from geometry_resim import is_clean, hold_min, atr_pre, sgn, signed, FEE

STOP_KS = [1.0, 1.5, 2.0, 2.5, 3.0]
TRAIL_KS = [0.5, 1.0, 1.5, 2.0]


def walk_v3(path, atr, stop_k, trail_k, partial):
    bars, ei, end_i, s, e = path
    if atr <= 0:
        return None
    R = stop_k * atr
    r_pct = R / e * 100
    stop = e - s * R
    peak = e
    armed = False
    half = None
    for _, o, h, l, c in bars[ei:end_i]:
        if (s > 0 and l <= stop) or (s < 0 and h >= stop):
            ex = signed(stop, e, s) - FEE
            return 0.5 * half + 0.5 * ex if (partial and half is not None) else ex
        fav = h if s > 0 else l
        if (s > 0 and fav > peak) or (s < 0 and fav < peak):
            peak = fav
        if abs(signed(peak, e, s)) >= r_pct:
            if partial and half is None:
                half = signed(e + s * R, e, s) - FEE
            armed = True
        if armed:
            stop = peak - s * trail_k * atr
    ex = signed(bars[end_i - 1][4], e, s) - FEE
    return 0.5 * half + 0.5 * ex if (partial and half is not None) else ex


def build_paths(ch):
    out = []
    for t in sorted((x for x in load_trades() if x["ch"] == ch and is_clean(x)), key=lambda x: x["t0"]):
        H = hold_min(t)
        ticks = load_ticks(t["sym"], t["t0"] - 25 * 60000, t["t0"] + (2 * H + 10) * 60000)
        bars = resample(ticks)
        if len(bars) < 6:
            continue
        ei = next((i for i, b in enumerate(bars) if b[0] >= t["t0"]), 0)
        search_end = next((i for i, b in enumerate(bars) if b[0] >= t["t0"] + H * 60000), len(bars))
        e = t["entry"]; s = sgn(t["side"])
        # FILL-verify: enter at the bar where price actually reaches the entry level
        fi = next((i for i in range(ei, search_end)
                   if (s > 0 and bars[i][3] <= e) or (s < 0 and bars[i][2] >= e)), None)
        if fi is None:
            continue
        end_i = next((i for i, b in enumerate(bars) if b[0] >= bars[fi][0] + H * 60000), len(bars))
        if end_i <= fi + 1:
            continue
        out.append(((bars, fi, end_i, s, e), atr_pre(bars, fi)))
    return out


def stats(nets):
    nets = [x for x in nets if x is not None]
    n = len(nets)
    if not n:
        return None
    wins = [x for x in nets if x > 0]
    dn = -sum(x for x in nets if x <= 0)
    return {"n": n, "avg": sum(nets) / n, "wr": 100 * len(wins) / n,
            "pf": (sum(wins) / dn if dn else 99.9), "worst": min(nets)}


def sweep(paths, partial, split=0.7):
    k = int(len(paths) * split)
    rows = []
    for sk in STOP_KS:
        for tk in TRAIL_KS:
            nets = [walk_v3(p, atr, sk, tk, partial) for p, atr in paths]
            tr, ho = stats(nets[:k]), stats(nets[k:])
            rows.append((sk, tk, tr, ho))
    return rows


def heatmap(rows, title, fname):
    grid = [[0] * len(TRAIL_KS) for _ in STOP_KS]
    for sk, tk, tr, ho in rows:
        grid[STOP_KS.index(sk)][TRAIL_KS.index(tk)] = tr["avg"] if tr else 0
    fig, ax = plt.subplots(figsize=(7, 5.5))
    im = ax.imshow(grid, cmap="RdYlGn", aspect="auto", vmin=-0.3, vmax=0.4)
    ax.set_xticks(range(len(TRAIL_KS))); ax.set_xticklabels(TRAIL_KS)
    ax.set_yticks(range(len(STOP_KS))); ax.set_yticklabels(STOP_KS)
    ax.set_xlabel("трейл (ATR)"); ax.set_ylabel("стоп (ATR)")
    for i in range(len(STOP_KS)):
        for j in range(len(TRAIL_KS)):
            ax.text(j, i, f"{grid[i][j]:+.2f}", ha="center", va="center", fontsize=9)
    ax.set_title(title); fig.colorbar(im, label="avg net %/сделка (train)")
    fig.tight_layout(); fig.savefig(OUT / fname, dpi=110); plt.close(fig)


def report(ch, paths):
    print(f"\n{'='*70}\n{ch}: путей {len(paths)}  (train {int(len(paths)*0.7)} / holdout {len(paths)-int(len(paths)*0.7)})")
    for partial in (False, True):
        rows = sweep(paths, partial)
        tag = "+половина@1R" if partial else "трейл-только"
        ok = [r for r in rows if r[2] and r[3]]
        ok.sort(key=lambda r: -(r[3]["avg"]))   # by holdout avg
        print(f"\n--- {tag} | топ-5 по holdout ---")
        print(f"  {'stop':>4}{'trail':>6} | {'train avg/PF/WR':>20} | {'HOLDOUT avg/PF/WR':>22} | worst")
        for sk, tk, tr, ho in ok[:5]:
            print(f"  {sk:>4}{tk:>6} | {tr['avg']:+5.2f}/{tr['pf']:4.2f}/{tr['wr']:3.0f}%"
                  f" | {ho['avg']:+5.2f}/{ho['pf']:4.2f}/{ho['wr']:3.0f}% | {ho['worst']:+.1f}%")
        if ch == "main_ws" and not partial:
            heatmap(rows, f"{ch}: V3 свип (train avg net%/сделка) — ищем ПЛАТО, не шип", "21_sweep_heatmap.png")


def main():
    for ch in ("main_ws", "bb_fade"):
        paths = build_paths(ch)
        if paths:
            report(ch, paths)
    print("\nheatmap -> docs/geometry/21_sweep_heatmap.png")


if __name__ == "__main__":
    main()
