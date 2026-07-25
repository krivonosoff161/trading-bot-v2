"""
Pump day analysis — tape context per trade.
Usage: python scripts/analysis/pump_day_analysis.py [YYYY-MM-DD]
Default date: today UTC.
"""

import csv
import gzip
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / "logs" / "pump" / "pump_labels.jsonl"
TAPE_DIR = Path("E:/trading-data/ticks")
CONTEXT_BEFORE_MIN = 5
CONTEXT_AFTER_MIN = 2


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_labels(date_str: str) -> list[dict]:
    trades = []
    with LABELS.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("type") != "EXIT":
                continue
            if d.get("opened_at", "").startswith(date_str):
                trades.append(d)
    return trades


def load_tape(sym: str, date_str: str) -> list[dict]:
    gz = TAPE_DIR / sym / f"{date_str}.csv.gz"
    csv_path = TAPE_DIR / sym / f"{date_str}.csv"
    rows = []
    try:
        if gz.exists():
            with gzip.open(gz, "rt", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        elif csv_path.exists():
            with open(csv_path, encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
    except Exception:
        pass
    return rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def infer_side(trade: dict) -> str:
    entry, exit_, gross = trade["entry_price"], trade["exit_price"], trade["gross_pnl_pct"]
    if gross >= 0:
        return "buy" if exit_ > entry else "sell"
    return "buy" if exit_ < entry else "sell"


def build_candles(ticks: list[dict], start_ms: int, end_ms: int) -> dict:
    candles: dict[int, dict] = {}
    for t in ticks:
        try:
            ts = int(t["ts_ms"])
        except (ValueError, KeyError):
            continue
        if ts < start_ms or ts > end_ms:
            continue
        if t.get("side") == "GAP" or not t.get("price"):
            continue
        price = float(t["price"])
        size = float(t.get("size", 0) or 0)
        side = t.get("side", "")
        minute = ts // 60000

        if minute not in candles:
            candles[minute] = {"open": price, "high": price, "low": price, "close": price,
                               "buy_vol": 0.0, "sell_vol": 0.0}
        c = candles[minute]
        c["high"] = max(c["high"], price)
        c["low"] = min(c["low"], price)
        c["close"] = price
        if side == "buy":
            c["buy_vol"] += size
        elif side == "sell":
            c["sell_vol"] += size
    return candles


def min_to_str(minute: int) -> str:
    return datetime.fromtimestamp(minute * 60, tz=timezone.utc).strftime("%H:%M")


# ---------------------------------------------------------------------------
# Per-trade analysis
# ---------------------------------------------------------------------------

def print_trade(trade: dict, ticks: list[dict]) -> None:
    sym = trade["sym"].replace("-USDT-SWAP", "")
    side = infer_side(trade)
    result = trade["exit_reason"]
    net = trade["net_pnl_pct"]
    hold = int(trade["hold_min"])
    entry_p = trade["entry_price"]
    exit_p = trade["exit_price"]

    opened = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
    closed = datetime.fromisoformat(trade["closed_at"].replace("Z", "+00:00"))
    entry_ms = int(opened.timestamp() * 1000)
    exit_ms = int(closed.timestamp() * 1000)

    icon = "TP  " if result == "TP" else "SL  "
    direction = "LONG" if side == "buy" else "SHORT"
    print(f"\n  [{icon}] {sym} {direction} {net:+.2f}% {hold}m  "
          f"{opened.strftime('%H:%M')}→{closed.strftime('%H:%M')}  "
          f"entry={entry_p}  exit={exit_p}")

    candles = build_candles(
        ticks,
        entry_ms - CONTEXT_BEFORE_MIN * 60_000,
        exit_ms + CONTEXT_AFTER_MIN * 60_000,
    )
    if not candles:
        print("    (no tape data)")
        return

    entry_min = entry_ms // 60_000
    exit_min = exit_ms // 60_000

    for minute in sorted(candles.keys()):
        c = candles[minute]
        total_vol = c["buy_vol"] + c["sell_vol"]
        buy_pct = round(c["buy_vol"] / total_vol * 100) if total_vol > 0 else 0
        chg = (c["close"] - c["open"]) / c["open"] * 100 if c["open"] else 0
        arrow = "^" if chg >= 0 else "v"

        tag = ""
        if minute == entry_min:
            tag = "  ← ENTRY"
        elif minute == exit_min:
            tag = f"  ← {result}"

        print(f"    {min_to_str(minute)}  {arrow}{chg:+.2f}%  "
              f"vol={total_vol:6.0f}  buy%={buy_pct:3d}%{tag}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.utcnow().strftime("%Y-%m-%d")

    trades = load_labels(date_str)
    if not trades:
        print(f"No trades for {date_str}")
        return

    wins = sum(1 for t in trades if t["exit_reason"] == "TP")
    total_pnl = sum(t["net_pnl_pct"] for t in trades)
    wr = wins / len(trades) * 100

    print(f"\n{'='*60}")
    print(f"  PUMP DAY ANALYSIS  {date_str}")
    print(f"  Trades: {len(trades)}  WR: {wins}/{len(trades)} ({wr:.0f}%)  PnL: {total_pnl:+.2f}%")
    print(f"{'='*60}")

    # Group by sym to load tape once per sym
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_sym[t["sym"]].append(t)

    for sym in sorted(by_sym.keys()):
        ticks = load_tape(sym, date_str)
        for trade in sorted(by_sym[sym], key=lambda x: x["opened_at"]):
            print_trade(trade, ticks)

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
