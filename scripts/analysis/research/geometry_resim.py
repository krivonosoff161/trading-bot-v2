"""
geometry_resim.py - re-simulate exit/risk variants on real tape paths.

For each LIVE-channel trade (main_ws, bb_fade), clean of B1 cheap-coin
artifacts, replay 3 exit models on the same 1m candle path and compare:
  V1 "current"  - the signal's recorded SL/TP/hold (validation of baseline)
  V2 "oracle"   - exit at the favourable peak (ceiling = money left on table)
  V3 "atr+trail"- ATR stop, trail after +1R (cap risk, let winners run)

Read-only. Usage: python scripts/analysis/research/geometry_resim.py
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

FEE = 0.10            # %, round-trip proxy (constant across variants)
K_STOP = 1.5          # V3 stop distance in ATRs
TRAIL_K = 1.0         # V3 trail distance in ATRs once in profit
MAX_HOLD = {"FAST": 120, "SWING": 300, "FADE": 80}


def is_clean(t) -> bool:
    """Drop B1-corrupted / degenerate trades."""
    e, sl = t.get("entry"), t.get("sl")
    if not e or e < 1e-3:           # sub-cent -> round(x,4) bug zone
        return False
    if not sl or sl <= 0:
        return False
    d = abs(sl / e - 1)
    return 0.001 < d < 0.15         # sane stop distance


def hold_min(t) -> int:
    return MAX_HOLD.get(t.get("style") or "", 120)


def sgn(side) -> int:
    return 1 if side in ("long", "buy") else -1


def signed(price, entry, s) -> float:
    return s * (price - entry) / entry * 100


def atr_pre(bars, ei, n=20) -> float:
    seg = bars[max(0, ei - n):ei] or bars[:ei + 1]
    if not seg:
        return 0.0
    return sum(h - l for _, _, h, l, _ in seg) / len(seg)


def walk_current(bars, ei, t, end_i):
    s = sgn(t["side"]); e = t["entry"]; sl = t["sl"]; tp = t.get("tp")
    for _, o, h, l, c in bars[ei:end_i]:
        if (s > 0 and l <= sl) or (s < 0 and h >= sl):
            return signed(sl, e, s) - FEE
        if tp and ((s > 0 and h >= tp) or (s < 0 and l <= tp)):
            return signed(tp, e, s) - FEE
    return signed(bars[end_i - 1][4], e, s) - FEE


def walk_oracle(bars, ei, t, end_i):
    s = sgn(t["side"]); e = t["entry"]
    best = max((signed(h if s > 0 else l, e, s) for _, o, h, l, c in bars[ei:end_i]), default=0)
    return best - FEE


def walk_atr_trail(bars, ei, t, end_i, atr):
    s = sgn(t["side"]); e = t["entry"]
    if atr <= 0:
        return walk_current(bars, ei, t, end_i)
    R = K_STOP * atr
    stop = e - s * R
    peak = e
    armed = False
    for _, o, h, l, c in bars[ei:end_i]:
        fav = h if s > 0 else l
        if (s > 0 and fav > peak) or (s < 0 and fav < peak):
            peak = fav
        if not armed and abs(signed(peak, e, s)) >= signed(e + s * R, e, s):
            armed = True
        if armed:
            stop = peak - s * TRAIL_K * atr
        if (s > 0 and l <= stop) or (s < 0 and h >= stop):
            return signed(stop, e, s) - FEE
    return signed(bars[end_i - 1][4], e, s) - FEE


def agg(label, vals):
    n = len(vals)
    if not n:
        return f"  {label:14} n=0"
    wins = [v for v in vals if v > 0]
    wr = 100 * len(wins) / n
    avg = sum(vals) / n
    pf_up = sum(wins); pf_dn = -sum(v for v in vals if v <= 0)
    pf = pf_up / pf_dn if pf_dn else float("inf")
    return f"  {label:14} n={n:3}  sum={sum(vals):+7.1f}%  avg={avg:+5.2f}%  WR={wr:3.0f}%  PF={pf:4.2f}"


def _avg(v):
    return sum(v) / len(v) if v else 0.0


def chart_variants(res):
    fig, axes = plt.subplots(1, len(res), figsize=(11, 5))
    if len(res) == 1:
        axes = [axes]
    for ax, (ch, d) in zip(axes, res.items()):
        a1, a3, a2 = _avg(d["V1"]), _avg(d["V3"]), _avg(d["V2"])
        bars = ax.bar(["V1 сейчас", "V3 ATR+трейл"], [a1, a3], color=["#d62728", "#2ca02c"], width=0.55)
        ax.axhline(a2, ls="--", c="#7f7f7f")
        ax.text(0.5, a2, f"V2 потолок (oracle) {a2:+.2f}%", ha="center", va="bottom", fontsize=8, color="#555")
        ax.axhline(0, c="k", lw=0.7)
        for b, vals in zip(bars, (d["V1"], d["V3"])):
            v = b.get_height()
            wr = 100 * len([x for x in vals if x > 0]) / len(vals) if vals else 0
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:+.2f}%\nWR {wr:.0f}%",
                    ha="center", va="bottom" if v >= 0 else "top", fontsize=9)
        ax.set_title(f"{ch}  (n={len(d['V1'])})"); ax.set_ylabel("avg net % / сделка")
    fig.suptitle("Re-sim выхода на реальных тик-путях: текущий → новая формула (пунктир = потолок)")
    fig.tight_layout(); fig.savefig(OUT / "20_resim_variants.png", dpi=110); plt.close(fig)


def main():
    raw = load_trades()
    res = {}
    for ch in ("main_ws", "bb_fade"):
        trades = [t for t in raw if t["ch"] == ch]
        clean = [t for t in trades if is_clean(t)]
        v1 = []; v2 = []; v3 = []
        for t in clean:
            H = hold_min(t)
            ticks = load_ticks(t["sym"], t["t0"] - 25 * 60000, t["t0"] + (H + 5) * 60000)
            bars = resample(ticks)
            if len(bars) < 6:
                continue
            ei = next((i for i, b in enumerate(bars) if b[0] >= t["t0"]), 0)
            end_i = next((i for i, b in enumerate(bars) if b[0] >= t["t0"] + H * 60000), len(bars))
            if end_i <= ei + 1:
                continue
            atr = atr_pre(bars, ei)
            v1.append(walk_current(bars, ei, t, end_i))
            v2.append(walk_oracle(bars, ei, t, end_i))
            v3.append(walk_atr_trail(bars, ei, t, end_i, atr))
        print(f"\n===== {ch}  (всего {len(trades)}, чистых {len(clean)}, с тейпом {len(v1)}) =====")
        print(agg("V1 как сейчас", v1))
        print(agg("V2 потолок", v2))
        print(agg("V3 ATR+трейл", v3))
        res[ch] = {"V1": v1, "V2": v2, "V3": v3}
    chart_variants(res)
    print("\nкартинка -> docs/geometry/20_resim_variants.png")


if __name__ == "__main__":
    main()
