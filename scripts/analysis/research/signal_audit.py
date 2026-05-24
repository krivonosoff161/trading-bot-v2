"""
signal_audit.py - data-integrity audit of recorded signals vs tape.

Read-only. For every live-channel signal with tape coverage, cross-checks the
recorded entry/SL/TP against the actual tick path and flags interpretation /
data bugs:
  B1      - sub-cent coin, round(x,4) corrupts levels (|SL/entry-1| insane)
  ENTRY   - recorded entry far from tape price at entry time
  NOSTOP  - price crossed SL in tape but outcome != SL (stop not enforced)
  PHANTOM - SL/TP placed where price never goes (e.g. SL ~0.0002 on HMSTR)

Usage: python scripts/analysis/research/signal_audit.py
"""
from __future__ import annotations

import sys
import io

if not getattr(sys, "_utf8_wrapped", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._utf8_wrapped = True

from geometry_model import load_ticks, load_trades

MAX_SANE_STOP = 0.15     # >15% stop distance = degenerate/corrupt
ENTRY_TOL = 0.6          # %, recorded entry vs tape-at-entry


def audit_trade(t):
    e = t.get("entry"); sl = t.get("sl"); side = t["side"]
    s = 1 if side in ("long", "buy") else -1
    ticks = load_ticks(t["sym"], t["t0"] - 60000, t["t1"] + 60000)
    if len(ticks) < 3:
        return None
    # tape price at/after entry
    px0 = next((p for ts, p in ticks if ts >= t["t0"]), ticks[0][1])
    hi = max(p for _, p in ticks); lo = min(p for _, p in ticks)
    flags = []
    if e and e < 1e-3:
        flags.append("B1")
    if e and sl:
        d = abs(sl / e - 1)
        if d > MAX_SANE_STOP or d < 1e-4:
            flags.append("PHANTOM")          # stop nowhere near price
    if e and px0:
        if abs(e - px0) / e * 100 > ENTRY_TOL:
            flags.append("ENTRY")            # recorded entry != tape price
    # stop enforcement: did price cross SL but outcome wasn't SL?
    if sl and t.get("outcome") not in ("SL", "sl", None):
        crossed = (s > 0 and lo <= sl) or (s < 0 and hi >= sl)
        if crossed:
            flags.append("NOSTOP")
    return flags


def main():
    raw = load_trades()
    for ch in ("main_ws", "bb_fade"):
        trades = [t for t in raw if t["ch"] == ch]
        audited = dirty = 0
        counts = {}; bad_coins = {}; examples = {}
        for t in trades:
            fl = audit_trade(t)
            if fl is None:
                continue
            audited += 1
            if fl:
                dirty += 1
                sym = t["sym"].replace("-USDT-SWAP", "")
                bad_coins[sym] = bad_coins.get(sym, 0) + 1
                for f in fl:
                    counts[f] = counts.get(f, 0) + 1
                    examples.setdefault(f, sym)
        print(f"\n===== {ch}: с тейпом {audited} =====")
        print(f"  чистых: {audited - dirty}/{audited}  |  с флагами: {dirty} ({100*dirty//max(audited,1)}%)")
        for f in ("B1", "PHANTOM", "ENTRY", "NOSTOP"):
            if counts.get(f):
                print(f"  {f:8} {counts[f]:3}  (пример: {examples.get(f)})")
        if bad_coins:
            top = sorted(bad_coins.items(), key=lambda x: -x[1])[:8]
            print(f"  проблемные монеты: {', '.join(f'{c}×{n}' for c, n in top)}")


if __name__ == "__main__":
    main()
