"""
BB Fade outcome labeler — reads bb_fade_signals.jsonl, checks OKX candle history,
writes outcome (TP/SL/TIME) and net_pct back into the file.

Usage:
    python scripts/analysis/bb_fade_label_outcomes.py
"""

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SIGNALS_LOG = ROOT / "logs" / "bb_fade" / "bb_fade_signals.jsonl"
OKX_CANDLES = "https://www.okx.com/api/v5/market/history-candles"
FEE_RT      = 0.10 / 100
BAR_MS      = 5 * 60 * 1000   # 5m bars for simulation


async def fetch_candles(session: aiohttp.ClientSession, sym: str,
                        after_ms: int, limit: int = 50) -> list:
    params = {"instId": sym, "bar": "5m", "after": str(after_ms + limit * BAR_MS), "limit": str(limit)}
    async with session.get(OKX_CANDLES, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        data = await resp.json(content_type=None)
    if data.get("code") != "0":
        return []
    return sorted(data.get("data", []), key=lambda c: int(c[0]))


def simulate_outcome(candles: list, side: str, entry: float,
                     tp: float, sl: float, hold_min: int) -> tuple[str, float]:
    max_bars = hold_min // 5
    for i, c in enumerate(candles[:max_bars]):
        h, l = float(c[2]), float(c[3])
        if side == "sell":
            if h >= sl:
                return "SL", (entry - sl) / entry * 100 - FEE_RT * 100
            if l <= tp:
                return "TP", (entry - tp) / entry * 100 - FEE_RT * 100
        else:
            if l <= sl:
                return "SL", (sl - entry) / entry * 100 - FEE_RT * 100
            if h >= tp:
                return "TP", (tp - entry) / entry * 100 - FEE_RT * 100
    exit_p = float(candles[min(max_bars - 1, len(candles) - 1)][4]) if candles else entry
    gross  = ((exit_p - entry) / entry * 100) * (1 if side == "buy" else -1)
    return "TIME", gross - FEE_RT * 100


async def label_all() -> None:
    if not SIGNALS_LOG.exists():
        print("No signals log found:", SIGNALS_LOG)
        return

    signals = []
    with SIGNALS_LOG.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                signals.append(json.loads(line))

    unlabeled = [s for s in signals if s.get("outcome") is None]
    now_ms = int(time.time() * 1000)
    ready = []
    for s in unlabeled:
        ts = datetime.fromisoformat(s["ts"].replace("Z", "+00:00"))
        age_min = (datetime.now(timezone.utc) - ts).total_seconds() / 60
        if age_min >= s.get("hold_min", 80) + 5:
            ready.append(s)

    print(f"Total signals: {len(signals)}  Unlabeled: {len(unlabeled)}  Ready to label: {len(ready)}")
    if not ready:
        print("Nothing to label yet.")
        return

    labeled_count = 0
    async with aiohttp.ClientSession() as session:
        for s in ready:
            sym     = s["symbol"]
            side    = s["side"]
            entry   = float(s["entry"])
            tp      = float(s["tp"])
            sl      = float(s["sl"])
            hold    = int(s.get("hold_min", 80))

            ts_ms = int(
                datetime.fromisoformat(s["ts"].replace("Z", "+00:00")).timestamp() * 1000
            )

            candles = await fetch_candles(session, sym, ts_ms, limit=hold // 5 + 5)
            if not candles:
                print(f"  SKIP {sym} — no candle data")
                continue

            outcome, net = simulate_outcome(candles, side, entry, tp, sl, hold)
            s["outcome"] = outcome
            s["net_pct"] = round(net, 4)
            labeled_count += 1
            print(f"  {sym} {side.upper()} -> {outcome}  net={net:+.3f}%")
            await asyncio.sleep(0.2)

    if labeled_count:
        with SIGNALS_LOG.open("w", encoding="utf-8") as f:
            for s in signals:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        print(f"\nLabeled {labeled_count} signals.")

    # Summary of all labeled
    done = [s for s in signals if s.get("outcome") is not None and s["outcome"] != "TIME"]
    if done:
        tp_n  = sum(1 for s in done if s["outcome"] == "TP")
        sl_n  = sum(1 for s in done if s["outcome"] == "SL")
        wr    = tp_n / len(done) * 100
        avg   = sum(s["net_pct"] for s in done) / len(done)
        pf_n  = sum(s["net_pct"] for s in done if s["outcome"] == "TP") or 0
        pf_d  = abs(sum(s["net_pct"] for s in done if s["outcome"] == "SL")) or 1
        print(f"\n=== BB Fade Live Stats ===")
        print(f"Labeled (excl TIME): {len(done)}  TP={tp_n}  SL={sl_n}")
        print(f"WR:     {wr:.1f}%")
        print(f"Avg net: {avg:+.3f}%")
        print(f"PF:     {pf_n / pf_d:.2f}")


if __name__ == "__main__":
    asyncio.run(label_all())
