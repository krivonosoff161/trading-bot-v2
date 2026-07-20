"""
label_signals.py — auto-label ENTRY/WAIT signal outcomes.

Scans both signal sources:
  logs/scanner/*/           — auto-scanner signals
  logs/users/*/analyses/*/  — manual user analysis signals

For each unlabeled ENTRY/WAIT signal fetches forward 15m candles from OKX
and determines outcome: TP1 | SL | TIME.
Writes result back into snapshot.json → llm_context.outcome.

Run: python scripts/label_signals.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.exchange.okx_client import OKXClient  # noqa: E402
from src.utils.runtime_root import load_runtime_dotenv  # noqa: E402

if __name__ == "__main__":
    load_runtime_dotenv(ROOT)

LOGS_DIR  = Path(__file__).parent.parent / "logs"
BAR       = "15m"
BAR_MS    = 15 * 60 * 1000
BUFFER_MS = 30 * 60 * 1000   # extra buffer after max_hold to ensure full window


def _find_snapshots():
    paths = []
    paths += [(p, "scanner") for p in sorted(LOGS_DIR.glob("scanner/*/*_snapshot.json"))]
    paths += [(p, "user")    for p in sorted(LOGS_DIR.glob("users/*/analyses/*/*_snapshot.json"))]
    return paths


def _check_outcome(candles_newest_first, side, entry, sl, tp1, signal_ts_ms, max_hold_ms):
    """Walk candles chronologically, return (outcome, exit_price, elapsed_min)."""
    for c in reversed(candles_newest_first):
        ts_c = int(c[0])
        if ts_c <= signal_ts_ms:
            continue
        high = float(c[2])
        low  = float(c[3])
        elapsed_ms = ts_c - signal_ts_ms
        if elapsed_ms > max_hold_ms:
            return "TIME", float(c[4]), elapsed_ms // 60000
        if side == "buy":
            if low  <= sl:   return "SL",  sl,  elapsed_ms // 60000
            if high >= tp1:  return "TP1", tp1, elapsed_ms // 60000
        else:
            if high >= sl:   return "SL",  sl,  elapsed_ms // 60000
            if low  <= tp1:  return "TP1", tp1, elapsed_ms // 60000
    return "OPEN", None, None


async def label_all(client: OKXClient):
    snaps = _find_snapshots()
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)

    labeled = skipped_recent = skipped_no_entry = already_done = 0

    for snap_path, source in snaps:
        with open(snap_path, encoding="utf-8") as f:
            data = json.load(f)

        ctx = data.get("llm_context", {})
        if ctx.get("entry_signal") not in ("ENTRY", "WAIT"):
            skipped_no_entry += 1
            continue
        if ctx.get("outcome"):
            already_done += 1
            continue

        entry_price = ctx.get("entry_price")
        sl_price    = ctx.get("sl_price")
        tp1_price   = ctx.get("tp1_price")
        side        = ctx.get("side")
        max_hold    = int(ctx.get("max_hold_minutes") or 120)
        captured_at = data.get("captured_at", "")

        if not all([entry_price, sl_price, tp1_price, side, captured_at]):
            skipped_no_entry += 1
            continue

        try:
            sig_ts = int(datetime.fromisoformat(
                captured_at.replace("Z", "+00:00")).timestamp() * 1000)
        except Exception:
            skipped_no_entry += 1
            continue

        max_hold_ms  = max_hold * 60 * 1000
        window_end   = sig_ts + max_hold_ms + BUFFER_MS

        if window_end > now_ms:
            skipped_recent += 1
            continue

        # Fetch forward candles (newest-first, covering signal → window_end)
        candles = await client.get_history_candles(
            data["symbol"], BAR, after=window_end, limit=max_hold * 4 // 15 + 5
        )
        await asyncio.sleep(0.15)

        outcome, exit_price, elapsed = _check_outcome(
            candles, side, entry_price, sl_price, tp1_price, sig_ts, max_hold_ms
        )

        if outcome == "OPEN":
            skipped_recent += 1
            continue

        ctx["outcome"]       = outcome
        ctx["outcome_price"] = round(exit_price, 6) if exit_price else None
        ctx["outcome_min"]   = elapsed
        ctx["outcome_src"]   = source
        ctx["labeled_at"]    = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        snap_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        labeled += 1

        sl_pct = abs(entry_price - sl_price) / entry_price * 100
        print(f"  [{source:7s}] {data['symbol']:12s} {captured_at[:16]}  "
              f"{side:4s}  {outcome:4s}  {elapsed}min  SL%={sl_pct:.2f}")

    print(f"\nDone: labeled={labeled}  already_done={already_done}  "
          f"too_recent={skipped_recent}  no_entry={skipped_no_entry}")


async def main():
    client = OKXClient(
        api_key    = os.getenv("OKX_API_KEY",    "").strip("'\""),
        secret_key = os.getenv("OKX_SECRET_KEY", "").strip("'\""),
        passphrase = os.getenv("OKX_PASSPHRASE", "").strip("'\""),
        is_demo    = os.getenv("OKX_IS_DEMO", "1") == "1",
    )
    try:
        print("Scanning logs/scanner/ and logs/users/*/analyses/ ...")
        await label_all(client)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
