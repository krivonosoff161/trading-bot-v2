"""
label_main_ws.py — fetch outcomes for unlabeled main screener signals.

Reads:  logs/signals/main_signals.jsonl
Writes: logs/signals/main_signals_labels.jsonl
"""
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

ROOT         = Path(__file__).resolve().parents[2]
MAIN_SIGNALS = ROOT / "logs" / "signals" / "main_signals.jsonl"
MAIN_LABELS  = ROOT / "logs" / "signals" / "main_signals_labels.jsonl"
REST_HISTORY = "https://www.okx.com/api/v5/market/history-candles"
BUFFER_MS    = 3_600_000  # wait 1h after hold expires before labeling


def _load_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _ts_ms(ts_str: str) -> int:
    return int(datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp() * 1000)


async def _fetch_candles(session: aiohttp.ClientSession, sym: str, after_ms: int, limit: int) -> list:
    params = {"instId": sym, "bar": "15m", "after": str(after_ms), "limit": str(limit)}
    async with session.get(REST_HISTORY, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
        body = await r.json(content_type=None)
    rows = body.get("data", [])
    rows.reverse()
    return rows


def _check_outcome(rows: list, signal_ms: int, hold_ms: int,
                   entry: float, sl: float, tp1: float, tp2: float,
                   side: str = "buy"):
    """Direction-aware outcome check — MARKET-entry semantics (matches production).

    Production bot uses ordType='market' (okx_client.py:300). Position opens
    immediately on the bar AFTER the signal, at market — no waiting for limit
    touch. SL/TP scored from that bar onwards within hold window.

    Slippage on market entry is NOT modeled here (~0.05% majors / 0.10% alts);
    it is approximated in NET via FEE_RT in downstream calcs (build_journal/tests).

    BUY:  SL hit when LOW <= SL;  TP when HIGH >= TP.
    SELL: SL hit when HIGH >= SL; TP when LOW <= TP.
    """
    is_buy = side == "buy"
    last_close = entry  # fallback if no in-window bar
    for row in rows:
        t_ms = int(row[0])  # OKX 15m candle OPEN time
        # Skip only bars that CLOSED at/before the market fill. The FIRST live bar
        # [fill, fill+15m] MUST be scored — that is where early SL/TP hits happen
        # (the old `t_ms <= signal_ms` dropped it and hid early stops).
        if t_ms + 15 * 60_000 <= signal_ms:
            continue
        if t_ms - signal_ms > hold_ms:
            break
        high = float(row[2])
        low  = float(row[3])
        last_close = float(row[4])  # close of last in-window bar -> TIME exit price

        if is_buy:
            sl_hit  = low <= sl
            tp1_hit = high >= tp1
            tp2_hit = bool(tp2 and high >= tp2)
        else:
            sl_hit  = high >= sl
            tp1_hit = low <= tp1
            tp2_hit = bool(tp2 and low <= tp2)

        if sl_hit and not tp1_hit:  # conservative same-bar tie: SL first
            return "SL", sl, round((t_ms - signal_ms) / 60_000), False
        if tp1_hit:
            outcome = "TP2" if tp2_hit else "TP1"
            exit_px = tp2 if tp2_hit else tp1
            return outcome, exit_px, round((t_ms - signal_ms) / 60_000), tp2_hit

    # TIME: real market exit at the close of the last in-window bar (was None ->
    # survivorship: TIME silently dropped from WR and sumR). Now a real signed outcome.
    return "TIME", last_close, hold_ms // 60_000, False


async def run() -> None:
    signals = _load_jsonl(MAIN_SIGNALS)
    if not signals:
        print("main_signals.jsonl пустой.")
        return

    labeled_ids = {r["signal_id"] for r in _load_jsonl(MAIN_LABELS)}
    now_ms = int(time.time() * 1000)

    pending = []
    for sig in signals:
        sig_id = sig.get("id") or sig.get("signal_id", "")
        if not sig_id or sig_id in labeled_ids:
            continue
        ts_ms   = _ts_ms(sig["ts"])
        hold_ms = int(sig.get("hold_min", 75) * 60_000)
        if ts_ms + hold_ms + BUFFER_MS > now_ms:
            continue
        sig["_id"]      = sig_id
        sig["_ts_ms"]   = ts_ms
        sig["_hold_ms"] = hold_ms
        pending.append(sig)

    if not pending:
        print("Нет записей для лейблинга.")
        return

    print(f"Лейблинг {len(pending)} сигналов...")
    labeled = 0

    async with aiohttp.ClientSession() as session:
        with open(MAIN_LABELS, "a", encoding="utf-8") as f:
            for sig in pending:
                try:
                    ts_ms   = sig["_ts_ms"]
                    hold_ms = sig["_hold_ms"]
                    # Cover the FULL hold window starting at/before the fill bar.
                    # (was +2h offset with limit hold//15+4 -> under-covered the first
                    # ~hour for SWING, so early bars were never fetched.) 30min buffer
                    # past hold-end for candle availability; +6 bars margin to reach ts.
                    after_ms = ts_ms + hold_ms + 30 * 60_000
                    limit    = sig.get("hold_min", 75) // 15 + 6

                    # Degenerate-geometry guard: sub-cent round(x,4) collisions where
                    # SL==TP1 (or SL within 0.15% of entry) make R meaningless. Exclude
                    # from metrics (outcome=INVALID) instead of scoring noise.
                    e_v = float(sig["entry"]); sl_v = float(sig["sl"]); tp1_v = float(sig["tp1"])
                    if e_v <= 0 or sl_v == tp1_v or abs(e_v - sl_v) / e_v < 0.0015:
                        f.write(json.dumps({
                            "signal_id": sig["_id"], "ts": sig["ts"], "symbol": sig["symbol"],
                            "regime": sig.get("regime", ""), "outcome": "INVALID",
                            "exit_price": None, "hold_min": 0, "tp2_hit": False,
                            "labeled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        }) + "\n")
                        labeled += 1
                        continue

                    rows = await _fetch_candles(session, sig["symbol"], after_ms, limit)
                    await asyncio.sleep(0.15)

                    outcome, exit_px, hold_actual, tp2_hit = _check_outcome(
                        rows, ts_ms, hold_ms,
                        float(sig["entry"]), float(sig["sl"]),
                        float(sig["tp1"]), float(sig.get("tp2") or 0),
                        side=sig.get("side", "buy"),
                    )

                    label = {
                        "signal_id": sig["_id"],
                        "ts":        sig["ts"],
                        "symbol":    sig["symbol"],
                        "regime":    sig.get("regime", ""),
                        "outcome":   outcome,
                        "exit_price": exit_px,
                        "hold_min":  hold_actual,
                        "tp2_hit":   tp2_hit,
                        "labeled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                    f.write(json.dumps(label) + "\n")
                    labeled += 1
                    sym = sig["symbol"].replace("-USDT-SWAP", "")
                    print(f"  {sym:<8} {sig['ts'][11:16]} -> {outcome}  exit={exit_px}")
                except Exception as e:
                    print(f"  ОШИБКА {sig.get('_id', '?')}: {e} — пропуск")

    print(f"Готово: {labeled} записей -> {MAIN_LABELS.name}")


if __name__ == "__main__":
    asyncio.run(run())
