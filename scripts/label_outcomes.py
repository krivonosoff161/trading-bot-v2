"""
label_outcomes.py — fetch outcomes for unlabeled entries in signal_log.jsonl.

Reads:  scripts/signal_log.jsonl   (append-only, written by telegram_bot.py)
Writes: scripts/signal_labels.jsonl (one outcome record per signal_id)

Run manually or on a cron after trading hours:
    python scripts/label_outcomes.py

Logic:
- Load signal_log.jsonl → list of signal dicts
- Load signal_labels.jsonl → set of already-labeled signal_ids
- For each unlabeled signal where ts_ms + max_hold_min*60*1000 + BUFFER_MS < now:
    fetch 5m forward candles from OKX
    compute outcome via check_outcome() (same function as backtest_simulate.py)
    append result to signal_labels.jsonl
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from src.exchange.okx_client import OKXClient
from scripts.backtest_simulate import check_outcome

SIGNAL_LOG    = Path(__file__).parent / "signal_log.jsonl"
SIGNAL_LABELS = Path(__file__).parent / "signal_labels.jsonl"
OUTCOME_H     = 24          # max forward window to fetch (hours)
BUFFER_MS     = 3_600_000   # 1h extra buffer before attempting label


def _load_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


async def run(logger=None) -> None:
    """Label pending signals. Optional logger(str) callback for external log sinks."""
    def _log(msg: str) -> None:
        print(msg)
        if logger:
            logger(msg)

    signals = _load_jsonl(SIGNAL_LOG)
    if not signals:
        _log("signal_log.jsonl пустой или не существует.")
        return

    labeled_ids = {r["signal_id"] for r in _load_jsonl(SIGNAL_LABELS)}
    now_ms      = int(time.time() * 1000)

    pending = [
        s for s in signals
        if s.get("signal_id") not in labeled_ids
        and s.get("ts_ms") is not None
        and s.get("close") is not None
        and s.get("sl") is not None
        and s.get("tp") is not None
        and (s["ts_ms"] + (s.get("max_hold_min") or 240) * 60_000 + BUFFER_MS) < now_ms
    ]

    if not pending:
        _log("Нет записей для лейблинга.")
        return

    _log(f"Лейблинг {len(pending)} сигналов...")

    client = OKXClient(
        api_key=os.getenv("OKX_API_KEY", ""),
        secret_key=os.getenv("OKX_SECRET_KEY", ""),
        passphrase=os.getenv("OKX_PASSPHRASE", ""),
        is_demo=False,
    )

    labeled = 0
    try:
        with open(SIGNAL_LABELS, "a", encoding="utf-8") as out_f:
            for sig in pending:
                try:
                    ts_ms       = sig["ts_ms"]
                    symbol      = sig["symbol"]
                    side        = sig["side"]
                    close       = float(sig["close"])
                    sl          = float(sig["sl"])
                    tp          = float(sig["tp"])
                    max_hold_ms = int((sig.get("max_hold_min") or 240) * 60_000)

                    fwd_end_ms = ts_ms + OUTCOME_H * 3_600_000 + 15 * 60_000
                    raw_fwd = await client.get_history_candles(symbol, "5m", after=fwd_end_ms, limit=288)
                    await asyncio.sleep(0.2)

                    outcome, elapsed_m, exit_price, mfe_r = check_outcome(
                        raw_fwd, side, sl, tp, ts_ms, max_hold_ms, entry_price=close,
                    )

                    sl_dist = abs(close - sl)
                    exit_r  = None
                    if exit_price is not None and sl_dist > 0:
                        direction = 1 if side == "buy" else -1
                        exit_r    = round(direction * (exit_price - close) / sl_dist, 2)

                    # Compute MAE (max adverse excursion in R units)
                    mae_r = 0.0
                    if sl_dist > 0:
                        mae = 0.0
                        for _c in reversed(raw_fwd):
                            _ts = int(_c[0])
                            if _ts <= ts_ms:
                                continue
                            if _ts > ts_ms + max_hold_ms:
                                break
                            _unf = (close - float(_c[3])) if side == "buy" else (float(_c[2]) - close)
                            mae  = max(mae, _unf)
                        mae_r = round(mae / sl_dist, 2)

                    label = {
                        "signal_id":  sig["signal_id"],
                        "ts_ms":      ts_ms,
                        "symbol":     symbol,
                        "side":       side,
                        "outcome":    outcome,
                        "elapsed_m":  elapsed_m,
                        "exit_price": exit_price,
                        "exit_r":     exit_r,
                        "mfe_r":      mfe_r,
                        "mae_r":      mae_r,
                        "labeled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                    out_f.write(json.dumps(label) + "\n")
                    labeled += 1
                    _log(
                        f"  {symbol} {side} "
                        f"{datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc).strftime('%m-%d %H:%M')}"
                        f" → {outcome}  exit_r={exit_r}  mfe_r={mfe_r}"
                    )
                except Exception as e:
                    _log(f"  ОШИБКА {sig.get('signal_id','?')}: {e} — пропуск")
    finally:
        await client.close()

    _log(f"Готово: {labeled} записей → {SIGNAL_LABELS.name}")


if __name__ == "__main__":
    asyncio.run(run())
