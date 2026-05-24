"""
geometry_cases.py - per-trade candlestick case charts (live channels).

Renders 1m candles around each entry from the local tick tape: candles before
AND after entry, with entry line, SL/TP, exit marker, MFE peak. Same style as
the earlier continuation case charts. Read-only.

Output: docs/geometry/cases/<ch>_<NN>_<SYM>_<tag>.png
Usage:  python scripts/analysis/research/geometry_cases.py
"""
from __future__ import annotations

import sys
import io
import datetime as dt

if not getattr(sys, "_utf8_wrapped", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._utf8_wrapped = True

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from geometry_model import load_ticks, load_trades, enrich, OUT

CASES = OUT / "cases"
PRE_MIN = 12        # candles before entry
POST_MIN = 18       # candles after exit
BAR_SEC = 60


def resample(ticks, bar_sec=BAR_SEC):
    """ticks [(ts_ms, price)] -> [(ts_ms, o, h, l, c)] bars."""
    bars = {}
    for ts, p in ticks:
        k = ts // (bar_sec * 1000)
        b = bars.get(k)
        if b is None:
            bars[k] = [p, p, p, p]
        else:
            b[1] = max(b[1], p); b[2] = min(b[2], p); b[3] = p
    return [(k * bar_sec * 1000, o, h, l, c) for k, (o, h, l, c) in sorted(bars.items())]


def _bar_idx(bars, ts_ms):
    for i, b in enumerate(bars):
        if b[0] >= ts_ms:
            return i
    return len(bars) - 1


def plot_case(t, n):
    t0, t1 = t["t0"], t["t1"]
    ticks = load_ticks(t["sym"], t0 - PRE_MIN * 60000, t1 + POST_MIN * 60000)
    bars = resample(ticks)
    if len(bars) < 5:
        return None
    sym = t["sym"].replace("-USDT-SWAP", "")
    fig, ax = plt.subplots(figsize=(13, 6))
    for x, (_, o, h, l, c) in enumerate(bars):
        up = c >= o
        col = "#2ca02c" if up else "#d62728"
        ax.vlines(x, l, h, color=col, lw=0.9, zorder=2)
        ax.bar(x, max(abs(c - o), (h - l) * 0.02), bottom=min(o, c),
               width=0.7, color=col, edgecolor=col, zorder=3)
    ei = _bar_idx(bars, t0); xi = _bar_idx(bars, t1)
    ax.axvspan(ei - 0.5, ei + 0.5, color="#f5d76e", alpha=0.35, zorder=1, label="свеча входа")
    ax.axhline(t["entry"], ls="--", c="#1f77b4", lw=1.2, label="вход")
    if t.get("sl"):
        ax.axhline(t["sl"], ls="--", c="#d62728", lw=1.0, label="SL")
    if t.get("tp"):
        ax.axhline(t["tp"], ls=":", c="#2ca02c", lw=1.2, label="TP")
    ax.axvline(xi, ls=":", c="k", lw=1.0, label="выход")
    # MFE peak marker
    sgn = 1 if t["side"] in ("long", "buy") else -1
    peak = max(bars[ei:], key=lambda b: sgn * b[2 if sgn > 0 else 3]) if bars[ei:] else None
    if peak:
        ax.scatter(_bar_idx(bars, peak[0]), peak[2] if sgn > 0 else peak[3],
                   marker="*", s=160, c="#9467bd", zorder=5, label="пик MFE")
    # x labels HH:MM every 5 bars
    ticks_x = range(0, len(bars), 5)
    ax.set_xticks(list(ticks_x))
    ax.set_xticklabels([dt.datetime.utcfromtimestamp(bars[i][0] / 1000).strftime("%H:%M") for i in ticks_x])
    reg = f" {t['regime']}/{t['style']}" if t.get("regime") else (f" {t['style']}" if t.get("style") else "")
    ax.set_title(f"{t['ch']} {sym} {t['side']}{reg}  |  MFE +{t['mfe']:.2f}% / MAE {t['mae']:.2f}% / "
                 f"net {t['net']:+.2f}%  ({t.get('outcome','')})", fontsize=11)
    ax.legend(loc="best", fontsize=8); ax.grid(alpha=0.15)
    CASES.mkdir(parents=True, exist_ok=True)
    tag = ("WIN" if t["net"] > 0 else "LOSS")
    fn = CASES / f"{t['ch']}_{n:02d}_{sym}_{tag}.png"
    fig.tight_layout(); fig.savefig(fn, dpi=105); plt.close(fig)
    return fn.name


def pick(rows, ch):
    """Assortment: best net, worst net (fat-tails), best MFE for the channel."""
    g = [r for r in rows if r["ch"] == ch]
    if not g:
        return []
    g_net = sorted(g, key=lambda r: r["net"])
    g_mfe = sorted(g, key=lambda r: -r["mfe"])
    sel, seen = [], set()
    for r in g_net[:2] + g_net[-2:] + g_mfe[:2]:        # worst2 + best2 + biggest-move2
        key = (r["sym"], r["t0"])
        if key not in seen:
            seen.add(key); sel.append(r)
    return sel


def main():
    rows = [e for e in (enrich(t) for t in load_trades()) if e]
    n = 0
    made = []
    for ch in ("main_ws", "bb_fade"):
        for r in pick(rows, ch):
            n += 1
            name = plot_case(r, n)
            if name:
                made.append(name)
    print(f"сделано кейсов: {len(made)} -> docs/geometry/cases/")
    for m in made:
        print("  ", m)


if __name__ == "__main__":
    main()
