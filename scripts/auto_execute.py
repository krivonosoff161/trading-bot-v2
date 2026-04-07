"""
Auto-executor — opens positions on OKX demo account when ENTRY signal fires.

Triggered by scanner and run_latest_analysis.py.
Reads AUTO_TRADE, AUTO_TRADE_PCT, AUTO_TRADE_LEVERAGE from .env.
Saves open positions to logs/auto_positions.json for timeout tracking.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from src.exchange.okx_client import OKXClient  # noqa: E402

# ── Config ─────────────────────────────────────────────────────────────────────

AUTO_TRADE       = os.getenv("AUTO_TRADE", "false").lower() == "true"
AUTO_TRADE_PCT   = float(os.getenv("AUTO_TRADE_PCT", "0.02"))    # 2% of balance
AUTO_LEVERAGE    = int(os.getenv("AUTO_TRADE_LEVERAGE", "10"))

# Max allowed price movement AGAINST the trade since signal was generated.
# Directional: skip short if price dropped too far, skip long if price rose too far.
MAX_WORSE_DEVIATION_PCT = 0.003   # 0.3% — tune per-symbol later if needed

POSITIONS_FILE   = ROOT / "logs" / "auto_positions.json"

# OKX contract sizes (ctVal) — contracts per unit
_CT_VAL: dict[str, float] = {
    "BTC-USDT":  0.01,
    "ETH-USDT":  0.1,
    "SOL-USDT":  1.0,
    "XRP-USDT":  100.0,
    "DOGE-USDT": 10.0,
    "ADA-USDT":  10.0,
}


def _make_client() -> OKXClient:
    return OKXClient(
        api_key    = os.getenv("OKX_API_KEY",    "").strip("'\""),
        secret_key = os.getenv("OKX_SECRET_KEY", "").strip("'\""),
        passphrase = os.getenv("OKX_PASSPHRASE", "").strip("'\""),
        is_demo    = True,
    )


def _load_positions() -> list[dict]:
    if POSITIONS_FILE.exists():
        try:
            return json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_positions(positions: list[dict]) -> None:
    POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    POSITIONS_FILE.write_text(json.dumps(positions, indent=2), encoding="utf-8")


def _calc_contracts(symbol: str, entry_price: float, balance: float) -> str:
    """Calculate number of contracts to open."""
    ct_val   = _CT_VAL.get(symbol, 1.0)
    notional = balance * AUTO_TRADE_PCT * AUTO_LEVERAGE
    contracts = notional / (entry_price * ct_val)
    contracts = max(1, round(contracts))
    return str(int(contracts))


async def execute_signal(result: dict) -> bool:
    """
    Open position based on analyze_chart.run() result.
    Returns True if order placed, False otherwise.
    """
    if not AUTO_TRADE:
        return False

    if result.get("entry_signal") != "ENTRY":
        return False

    symbol      = result["symbol"]
    side        = result.get("side")
    entry_price = result.get("entry_price")
    sl_price    = result.get("sl_price")
    tp1_price   = result.get("tp1_price")
    max_hold    = result.get("max_hold_min", 120)

    if not all([side, entry_price, sl_price, tp1_price]):
        print("auto_execute: missing levels — skip")
        return False

    client = _make_client()
    try:
        # Check for existing position on this pair
        positions = await client.get_positions(symbol)
        if positions:
            print(f"auto_execute: {symbol} already has open position — skip")
            return False

        # Deviation guard — check current price vs signal price
        current_price = await client.get_price(symbol)
        if current_price is None:
            print(f"auto_execute: {symbol} could not fetch current price — skip")
            return False

        deviation_pct = (current_price - float(entry_price)) / float(entry_price)
        # Directional: bad deviation = price moved AGAINST the trade
        # sell: bad if current < signal (price dropped, worse short entry)
        # buy:  bad if current > signal (price rose, worse long entry)
        worse_deviation = (-deviation_pct) if side == "sell" else deviation_pct
        skipped = worse_deviation > MAX_WORSE_DEVIATION_PCT

        print(
            f"auto_execute: {symbol} {side.upper()} | "
            f"signal={entry_price} current={current_price:.4f} "
            f"dev={deviation_pct:+.3%} worse={worse_deviation:.3%} "
            f"{'SKIP — price moved too far' if skipped else 'OK — within threshold'}"
        )
        if skipped:
            return False

        # Recalculate TP/SL anchored to current_price, preserving original risk distance
        orig_sl_dist = abs(float(entry_price) - float(sl_price))
        if side == "sell":
            fill_sl  = round(current_price + orig_sl_dist, 4)
            fill_tp1 = round(current_price - orig_sl_dist * 0.8, 4)
        else:
            fill_sl  = round(current_price - orig_sl_dist, 4)
            fill_tp1 = round(current_price + orig_sl_dist * 0.8, 4)

        print(
            f"auto_execute: TP/SL recalc from fill | "
            f"orig SL={sl_price} TP={tp1_price} → "
            f"new SL={fill_sl} TP={fill_tp1} (anchor={current_price:.4f})"
        )

        # Get balance and calculate size
        balance = await client.get_balance()
        if balance < 10:
            print(f"auto_execute: balance too low ({balance:.2f} USDT) — skip")
            return False

        contracts = _calc_contracts(symbol, current_price, balance)

        # Set leverage
        await client.set_leverage(symbol, AUTO_LEVERAGE)

        # Place order with OCO using recalculated levels
        order = await client.place_market_order(
            symbol   = symbol,
            side     = side,
            size     = contracts,
            tp_price = str(fill_tp1),
            sl_price = str(fill_sl),
        )
        if not order:
            print(f"auto_execute: order failed for {symbol}")
            return False

        # Save to positions tracker
        now = datetime.now(timezone.utc)
        close_after = now.timestamp() + max_hold * 60
        pos_record  = {
            "symbol":        symbol,
            "side":          side,
            "entry_time":    now.isoformat(),
            "close_after":   close_after,
            "contracts":     contracts,
            "signal_entry":  float(entry_price),
            "fill_anchor":   current_price,
            "deviation_pct": round(deviation_pct, 5),
            "sl":            str(fill_sl),
            "tp1":           str(fill_tp1),
            "ord_id":        order.get("ordId", ""),
        }
        existing = _load_positions()
        existing = [p for p in existing if p["symbol"] != symbol]  # drop stale same-symbol records
        existing.append(pos_record)
        _save_positions(existing)

        print(f"auto_execute: OK {symbol} {side.upper()} {contracts} contracts "
              f"| SL={fill_sl} TP={fill_tp1} | hold={max_hold}min")
        return True

    finally:
        await client.close()


async def check_and_close_timeouts() -> None:
    """Close positions that exceeded max hold time. Call from scanner loop."""
    positions = _load_positions()
    if not positions:
        return

    now       = datetime.now(timezone.utc).timestamp()
    remaining = []
    client    = _make_client()

    try:
        for pos in positions:
            if now < pos["close_after"]:
                remaining.append(pos)
                continue

            symbol   = pos["symbol"]
            our_side = pos["side"]          # "buy" or "sell"
            our_fill = pos.get("fill_anchor", 0.0)
            pos_side = "long" if our_side == "buy" else "short"

            open_pos = await client.get_positions(symbol)
            if not open_pos:
                print(f"auto_execute timeout: {symbol} already closed")
                continue

            # Safety: verify the open position belongs to this bot before closing.
            # In net mode: pos > 0 = long (buy), pos < 0 = short (sell).
            # avgPx must match our recorded fill_anchor within 0.5% tolerance.
            matched = None
            for p in open_pos:
                p_size = float(p.get("pos", 0))
                p_side = "buy" if p_size > 0 else "sell"
                if p_side != our_side:
                    continue
                avg_px = float(p.get("avgPx", 0))
                if avg_px > 0 and our_fill > 0:
                    if abs(avg_px - our_fill) / our_fill > 0.005:  # >0.5% diff — not our fill
                        continue
                matched = p
                break

            if matched is None:
                print(
                    f"auto_execute timeout: {symbol} — open position does NOT match "
                    f"our side={our_side} fill={our_fill:.4f} "
                    f"— SKIP close to avoid touching a manual position"
                )
                continue  # drop from tracker — our position is already gone

            ok = await client.close_position(symbol, pos_side)
            if ok:
                print(f"auto_execute timeout: closed {symbol} {our_side} {pos.get('contracts')} contracts")
            else:
                print(f"auto_execute timeout: ❌ failed to close {symbol} — keeping in tracker")
                remaining.append(pos)
    finally:
        await client.close()

    _save_positions(remaining)
