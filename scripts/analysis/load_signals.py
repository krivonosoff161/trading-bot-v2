"""
Load and join signals from signals.jsonl for backtesting.

Usage in backtest scripts:
    from scripts.load_signals import load_trades, load_no_trades

    trades = load_trades("logs_archive/logs_2026-03-08_12-00/signals.jsonl")
    for t in trades:
        print(t["symbol"], t["ema_gap"], t["pnl"], t["reason"])
"""

import json
from pathlib import Path
from typing import List


def load_trades(jsonl_path: str) -> List[dict]:
    """
    Load completed trades: OPEN events joined with their CLOSE events.

    Each returned dict has all OPEN fields (symbol, side, price, atr, ema_gap,
    adx, rsi, sl, tp, trade_id) plus CLOSE fields (pnl, reason, close_price).
    Only trades with both OPEN and CLOSE are returned.
    """
    opens: dict = {}
    trades: List[dict] = []

    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            event = rec.get("event")
            trade_id = rec.get("trade_id")

            if event == "open" and trade_id:
                opens[trade_id] = rec
            elif event == "close" and trade_id and trade_id in opens:
                trade = {**opens.pop(trade_id), **rec}
                trades.append(trade)

    return trades


def load_no_trades(jsonl_path: str) -> List[dict]:
    """Load all no_trade tick events (filtered/skipped signals)."""
    result: List[dict] = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("event") == "no_trade":
                result.append(rec)
    return result


def load_all(jsonl_path: str) -> List[dict]:
    """Load every event from signals.jsonl as-is."""
    result: List[dict] = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                result.append(json.loads(line))
    return result


def find_last_session(archive_dir: str = "logs_archive") -> str:
    """Return path to signals.jsonl of the most recent archived session."""
    sessions = sorted(Path(archive_dir).glob("logs_*/signals.jsonl"), reverse=True)
    if not sessions:
        raise FileNotFoundError(f"No signals.jsonl found in {archive_dir}")
    return str(sessions[0])
