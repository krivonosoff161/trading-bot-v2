from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import aiohttp
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.backtest.research_common import (  # noqa: E402
    ADX_PERIOD,
    SYMBOLS,
    arrays,
    bollinger_series,
    load_cache,
    map_by_ts,
    net_r,
    save_json,
    summarize,
)
from src.strategy.indicators import calc_adx, calc_atr_series, calc_slope  # noqa: E402


RESULT_PATH = Path(__file__).with_name("bt_bb_screener_results.json")
THREE_M_CACHE = Path(__file__).with_name("bt_bb_screener_3m_cache.json")
BASELINE_PF = 1.98
OKX_URL = "https://www.okx.com/api/v5/market/history-candles"


def safe_adx_series(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> np.ndarray:
    out = np.zeros(len(closes))
    for idx in range(ADX_PERIOD * 2, len(closes)):
        out[idx] = calc_adx(highs[: idx + 1], lows[: idx + 1], closes[: idx + 1], ADX_PERIOD)[0]
    return out


def step_outcome(rows: list, start: int, side: str, stop: float, target: float, hold_bars: int) -> tuple[str, float, int]:
    last_close = float(rows[start][4])
    for idx in range(start + 1, min(len(rows), start + hold_bars + 1)):
        high = float(rows[idx][2])
        low = float(rows[idx][3])
        last_close = float(rows[idx][4])
        if side == "buy":
            if low <= stop:
                return "STOP", stop, idx - start
            if high >= target:
                return "TP", target, idx - start
        else:
            if high >= stop:
                return "STOP", stop, idx - start
            if low <= target:
                return "TP", target, idx - start
    return "TIME_EXIT", last_close, hold_bars


def simulate_intraday(symbol: str, tf_rows: list, h1_rows: list, h15_rows: list, bar_minutes: int) -> list[dict]:
    ts, _o, _h, _l, closes, vols = arrays(tf_rows)
    h1_ts, h1_o, h1_h, h1_l, h1_c, _ = arrays(h1_rows)
    h15_ts, _o15, h15_h, h15_l, h15_c, _ = arrays(h15_rows)
    if len(ts) < 40 or len(h1_ts) < 30 or len(h15_ts) < 30:
        return []

    mid, upper, lower = bollinger_series(closes)
    vol_ma = np.zeros(len(vols))
    for idx in range(19, len(vols)):
        vol_ma[idx] = float(np.mean(vols[idx - 19:idx + 1]))

    atr_15 = calc_atr_series(h15_h, h15_l, h15_c, period=14)
    adx_h1 = safe_adx_series(h1_h, h1_l, h1_c)
    slope_15 = np.array([calc_slope(h15_c[: i + 1], period=5) for i in range(len(h15_ts))])
    adx_by_ts = map_by_ts(h1_ts, adx_h1)
    atr_by_ts = map_by_ts(h15_ts, atr_15)
    slope_by_ts = map_by_ts(h15_ts, slope_15)
    h1_keys = np.array(sorted(adx_by_ts))
    h15_keys = np.array(sorted(atr_by_ts))
    trades: list[dict] = []
    hold_bars = 120 // bar_minutes

    for idx in range(22, len(ts) - 1):
        if not upper[idx]:
            continue
        h1_idx = np.searchsorted(h1_keys, ts[idx], side="right") - 1
        h15_idx = np.searchsorted(h15_keys, ts[idx], side="right") - 1
        if h1_idx < 0 or h15_idx < 2:
            continue
        adx_now = adx_by_ts[int(h1_keys[h1_idx])]
        atr_now = atr_by_ts[int(h15_keys[h15_idx])]
        slope_now = slope_by_ts[int(h15_keys[h15_idx])]
        slope_prev2 = slope_by_ts[int(h15_keys[h15_idx - 2])]
        if adx_now >= 22 or atr_now <= 0 or abs(slope_now) >= abs(slope_prev2):
            continue
        prev_big_vol = vol_ma[idx - 1] > 0 and vols[idx - 1] > vol_ma[idx - 1] * 1.5
        prev_break_up = closes[idx - 1] > upper[idx - 1]
        prev_break_dn = closes[idx - 1] < lower[idx - 1]
        if closes[idx] < lower[idx] and not (prev_break_dn and prev_big_vol):
            side, target = "buy", mid[idx]
        elif closes[idx] > upper[idx] and not (prev_break_up and prev_big_vol):
            side, target = "sell", mid[idx]
        else:
            continue
        stop = closes[idx] - atr_now * 1.5 if side == "buy" else closes[idx] + atr_now * 1.5
        outcome, exit_price, bars = step_outcome(tf_rows, idx, side, stop, target, hold_bars)
        trades.append(
            {
                "symbol": symbol,
                "timeframe": f"{bar_minutes}m",
                "outcome": outcome,
                "hold_min": bars * bar_minutes,
                "result_r": net_r(side, closes[idx], exit_price, stop),
            }
        )
    return trades


def simulate_hourly(symbol: str, h1_rows: list, h4_rows: list) -> list[dict]:
    ts, _o, h1_h, h1_l, h1_c, _ = arrays(h1_rows)
    h4_ts, _o4, h4_h, h4_l, h4_c, _ = arrays(h4_rows)
    if len(ts) < 40 or len(h4_ts) < 30:
        return []
    mid, upper, lower = bollinger_series(h1_c)
    atr_1h = calc_atr_series(h1_h, h1_l, h1_c, period=14)
    adx_4h = safe_adx_series(h4_h, h4_l, h4_c)
    adx_by_ts = map_by_ts(h4_ts, adx_4h)
    h4_keys = np.array(sorted(adx_by_ts))
    trades: list[dict] = []

    for idx in range(21, len(ts) - 1):
        if not upper[idx] or atr_1h[idx] <= 0:
            continue
        h4_idx = np.searchsorted(h4_keys, ts[idx], side="right") - 1
        if h4_idx < 0 or adx_by_ts[int(h4_keys[h4_idx])] >= 20:
            continue
        long_touch = h1_c[idx] < lower[idx] and h1_c[idx - 1] < lower[idx - 1]
        short_touch = h1_c[idx] > upper[idx] and h1_c[idx - 1] > upper[idx - 1]
        if not (long_touch or short_touch):
            continue
        side = "buy" if long_touch else "sell"
        target = mid[idx]
        stop = h1_c[idx] - atr_1h[idx] * 1.5 if side == "buy" else h1_c[idx] + atr_1h[idx] * 1.5
        outcome, exit_price, bars = step_outcome(h1_rows, idx, side, stop, target, 8)
        trades.append(
            {
                "symbol": symbol,
                "timeframe": "1h",
                "outcome": outcome,
                "hold_min": bars * 60,
                "result_r": net_r(side, h1_c[idx], exit_price, stop),
            }
        )
    return trades


async def fetch_three_minute(symbol: str, start_ms: int) -> list:
    inst_id = f"{symbol}-SWAP"
    rows: dict[str, list] = {}
    after: str | None = None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        while True:
            params = {"instId": inst_id, "bar": "3m", "limit": "100"}
            if after is not None:
                params["after"] = after
            async with session.get(OKX_URL, params=params) as resp:
                payload = await resp.json(content_type=None)
            batch = payload.get("data", [])
            if not batch:
                break
            oldest = None
            for row in batch:
                ts_ms = int(row[0])
                oldest = ts_ms
                if row[8] == "1" and ts_ms >= start_ms:
                    rows[row[0]] = row
            if oldest is None or oldest < start_ms or len(batch) < 100:
                break
            after = str(oldest)
            await asyncio.sleep(0.1)
    return [rows[key] for key in sorted(rows, key=int)]


async def load_three_minute(cache: dict) -> dict[str, list]:
    if THREE_M_CACHE.exists():
        return json.loads(THREE_M_CACHE.read_text(encoding="utf-8"))
    start_ms = min(int(cache[sym]["5m"][0][0]) for sym in SYMBOLS)
    data = {sym: await fetch_three_minute(sym, start_ms) for sym in SYMBOLS}
    THREE_M_CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def pack_mode(trades_by_symbol: dict[str, list]) -> dict:
    summary = {sym: summarize(trades) for sym, trades in trades_by_symbol.items()}
    combined = summarize([trade for trades in trades_by_symbol.values() for trade in trades])
    return {"by_symbol": summary, "combined": combined}


async def main() -> None:
    cache = load_cache()
    cache_3m = await load_three_minute(cache)
    modes = {"5m": {}, "3m": {}, "1h": {}}
    for symbol in SYMBOLS:
        data = cache[symbol]
        modes["5m"][symbol] = simulate_intraday(symbol, data["5m"], data["1h"], data["15m"], 5)
        modes["3m"][symbol] = simulate_intraday(symbol, cache_3m.get(symbol, []), data["1h"], data["15m"], 3)
        modes["1h"][symbol] = simulate_hourly(symbol, data["1h"], data["4h"])

    payload = {
        "baseline_pf_3pairs_5m": BASELINE_PF,
        "modes": {name: pack_mode(trades) for name, trades in modes.items()},
    }
    save_json(RESULT_PATH, payload)

    print("BB Screener research")
    for mode in ("5m", "3m", "1h"):
        combined = payload["modes"][mode]["combined"]
        print(
            f"{mode:>2} | n={combined['n']:3} WR={combined['wr']:5.1f}% "
            f"PF={combined['pf']:4.2f} avg_R={combined['avg_r']:+.3f} hold={combined['avg_hold']:5.1f}m"
        )
        for symbol in SYMBOLS:
            row = payload["modes"][mode]["by_symbol"][symbol]
            print(
                f"  {symbol:<9} n={row['n']:3} WR={row['wr']:5.1f}% "
                f"PF={row['pf']:4.2f} avg_R={row['avg_r']:+.3f}"
            )
    print(f"Saved -> {RESULT_PATH.name}")


if __name__ == "__main__":
    asyncio.run(main())
