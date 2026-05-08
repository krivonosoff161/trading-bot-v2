from __future__ import annotations

import bisect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.backtest.backtest_simulate import _candle_vol_delta, _get_hist_funding, _get_oi_delta, compute_signal
from scripts.backtest.research_common import FEE_RT_PCT, load_cache, net_r, save_json, summarize
from src.strategy.indicators import find_fvg


RESULT_PATH = Path(__file__).with_name("bt_breakeven_results.json")
CONFIGS = [
    (0.3, 0.3, 1.5),
    (0.3, 0.3, 2.5),
    (0.5, 0.5, 1.5),
    (0.5, 0.5, 2.5),
    (0.5, 0.5, 3.0),
    (0.3, 0.3, "fvg"),
    (0.5, 0.5, "fvg"),
]


def visible(raw: list, end: int, size: int) -> list:
    return list(reversed(raw[max(0, end - size):end]))


def tp2_price(side: str, entry: float, stop: float, raw_15m: list, tp2_cfg) -> float:
    risk = abs(entry - stop)
    direction = 1 if side == "buy" else -1
    if tp2_cfg != "fvg":
        return entry + direction * risk * float(tp2_cfg)
    fvg_dir = "bear" if side == "buy" else "bull"
    for gap in find_fvg(list(reversed(raw_15m)), fvg_dir, lookback=30):
        dist_r = abs(gap["mid"] - entry) / risk if risk else 0.0
        valid = gap["mid"] > entry if side == "buy" else gap["mid"] < entry
        if valid and 0.5 <= dist_r <= 3.0:
            return gap["mid"]
    return entry + direction * risk * 2.5


def simulate_trade(raw_5m: list, ts_ms: int, side: str, entry: float, stop: float, tp1_r: float, be_r: float, tp2: float, hold_min: int) -> dict:
    risk = abs(entry - stop)
    tp1 = entry + risk * tp1_r if side == "buy" else entry - risk * tp1_r
    be_stop = entry * (1.0 + FEE_RT_PCT) if side == "buy" else entry * (1.0 - FEE_RT_PCT)
    triggered = False
    last_close = entry

    for row in raw_5m:
        ts = int(row[0])
        if ts <= ts_ms:
            continue
        hold = (ts - ts_ms) // 60000
        if hold > hold_min:
            return {"outcome": "TIME_EXIT", "exit_price": last_close, "hold_min": hold}
        high = float(row[2])
        low = float(row[3])
        last_close = float(row[4])
        if side == "buy":
            if not triggered and low <= stop:
                return {"outcome": "STOP", "exit_price": stop, "hold_min": hold}
            if not triggered and high >= tp1:
                triggered = True
            if triggered and high >= tp2:
                return {"outcome": "TP", "exit_price": tp2, "hold_min": hold}
            if triggered and low <= be_stop:
                return {"outcome": "BE_EXIT", "exit_price": be_stop, "hold_min": hold}
        else:
            if not triggered and high >= stop:
                return {"outcome": "STOP", "exit_price": stop, "hold_min": hold}
            if not triggered and low <= tp1:
                triggered = True
            if triggered and low <= tp2:
                return {"outcome": "TP", "exit_price": tp2, "hold_min": hold}
            if triggered and high >= be_stop:
                return {"outcome": "BE_EXIT", "exit_price": be_stop, "hold_min": hold}
    return {"outcome": "OPEN", "exit_price": last_close, "hold_min": hold_min}


def collect_signals(cache: dict) -> list[dict]:
    signals: list[dict] = []
    for symbol in [sym for sym in cache if isinstance(cache.get(sym), dict) and "15m" in cache[sym]]:
        data = cache[symbol]
        ts15 = [int(row[0]) for row in data["15m"]]
        ts4 = [int(row[0]) for row in data["4h"]]
        ts1 = [int(row[0]) for row in data["1h"]]
        ts5 = [int(row[0]) for row in data["5m"]]
        fund_hist = data.get("funding_history", [])
        oi_hist = data.get("oi_history", [])
        for ts_ms in ts15[96:-1]:
            i4 = bisect.bisect_left(ts4, ts_ms)
            i1 = bisect.bisect_left(ts1, ts_ms)
            i15 = bisect.bisect_left(ts15, ts_ms)
            i5 = bisect.bisect_left(ts5, ts_ms)
            raw_4h = visible(data["4h"], i4, 60)
            raw_1h = visible(data["1h"], i1, 60)
            raw_15m = visible(data["15m"], i15, 96)
            raw_5m = visible(data["5m"], i5, 30)
            sig = compute_signal(
                raw_4h,
                raw_1h,
                raw_15m,
                funding=_get_hist_funding(fund_hist, ts_ms),
                symbol=symbol,
                mode="COMBINED",
                oi_delta=_get_oi_delta(oi_hist, ts_ms),
                raw_5m=raw_5m,
                trade_delta_15m=_candle_vol_delta(raw_5m),
            )
            if not sig or sig["entry_signal"] != "ENTRY" or sig["regime"] not in ("DRIFT", "TRENDING", "WEAK_TREND"):
                continue
            signals.append(
                {
                    "symbol": symbol,
                    "ts_ms": ts_ms,
                    "side": sig["side"],
                    "entry": sig["close"],
                    "stop": sig["sl"],
                    "regime": sig["regime"],
                    "raw_15m": raw_15m,
                    "raw_5m_fwd": data["5m"][i5:],
                }
            )
    return sorted(signals, key=lambda row: (row["ts_ms"], row["symbol"]))


def main() -> None:
    cache = load_cache()
    signals = collect_signals(cache)
    payload = {"signal_count": len(signals), "configs": {}}

    for tp1_r, be_r, tp2_cfg in CONFIGS:
        label = f"tp1_{tp1_r}_be_{be_r}_tp2_{tp2_cfg}"
        trades = []
        for sig in signals:
            hold_min = 75 if sig["regime"] in ("DRIFT", "WEAK_TREND") else 90
            tp2 = tp2_price(sig["side"], sig["entry"], sig["stop"], sig["raw_15m"], tp2_cfg)
            result = simulate_trade(
                sig["raw_5m_fwd"],
                sig["ts_ms"],
                sig["side"],
                sig["entry"],
                sig["stop"],
                tp1_r,
                be_r,
                tp2,
                hold_min,
            )
            trades.append(
                {
                    "outcome": result["outcome"],
                    "hold_min": result["hold_min"],
                    "result_r": net_r(sig["side"], sig["entry"], result["exit_price"], sig["stop"]),
                }
            )
        summary = summarize(trades)
        payload["configs"][label] = {**summary, "tp1_r": tp1_r, "breakeven_trigger": be_r, "tp2_r": tp2_cfg}

    ranked = sorted(payload["configs"].items(), key=lambda item: (item[1]["pf"], item[1]["avg_r"]), reverse=True)
    payload["top3"] = [{"label": label, **stats} for label, stats in ranked[:3]]
    save_json(RESULT_PATH, payload)

    print(f"Signals: {len(signals)}")
    for row in payload["top3"]:
        print(
            f"{row['label']}: n={row['n']} WR={row['wr']:.1f}% PF={row['pf']:.2f} "
            f"avg_R={row['avg_r']:+.3f} TIME={row['time_exit_count']} BE={row['be_exit_count']}"
        )
    print(f"Saved -> {RESULT_PATH.name}")


if __name__ == "__main__":
    main()
