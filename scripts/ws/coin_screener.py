"""
Dynamic coin screener for OKX SWAP pump scanning.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bt_pump_core import DEFAULT_CACHE_DIR, detect_signals, analyze_forward
from bt_pump_filters import BASE_LOOKBACK, BASE_PRICE_PCT, BASE_VOL_MULT, load_frames, fetch_ctvals, precision, mean


LATEST_PATH = DEFAULT_CACHE_DIR / "coin_screener_latest.json"
HISTORY_PATH = DEFAULT_CACHE_DIR / "coin_screener_history.jsonl"
BACKTEST_PATH = DEFAULT_CACHE_DIR / "coin_screener_backtest.json"
TIER1 = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
TOP_N = 10
WATCH_N = 5
MIN_DOLLAR_VOL_24H = 10_000_000.0
MAX_VOLATILITY_24H = 50.0


def _is_excluded_symbol(inst_id: str) -> tuple[bool, str]:
    if not inst_id.endswith("-USDT-SWAP"):
        return True, "not_usdt_swap"
    upper = inst_id.upper()
    if any(token in upper for token in ("UP-USDT", "DOWN-USDT", "3L-USDT", "3S-USDT")):
        return True, "leveraged_token"
    if inst_id.count("-") > 2:
        return True, "exotic_name"
    return False, ""


def fetch_current_tickers() -> list[dict]:
    url = "https://www.okx.com/api/v5/market/tickers?" + urlencode({"instType": "SWAP"})
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if payload.get("code") != "0":
        raise RuntimeError(f"OKX tickers failed: code={payload.get('code')} msg={payload.get('msg', '')}")
    return payload.get("data", [])


def norm_log_dollar_vol(value: float, min_value: float = 10_000_000.0, max_value: float = 5_000_000_000.0) -> float:
    value = max(value, min_value)
    return float((math.log10(value) - math.log10(min_value)) / (math.log10(max_value) - math.log10(min_value)))


def volatility_score(volatility_24h: float) -> float:
    if volatility_24h < 5.0 or volatility_24h > 50.0:
        return 0.0
    center = 20.0
    width = 15.0
    score = math.exp(-((volatility_24h - center) ** 2) / (2 * width * width))
    return float(score)


def momentum_score(momentum_24h: float) -> float:
    clamped = min(max(momentum_24h, 0.0), 15.0)
    return float(clamped / 15.0)


def compute_score(dollar_vol_24h: float, volatility_24h: float, momentum_24h: float) -> float:
    return (
        0.4 * norm_log_dollar_vol(dollar_vol_24h) +
        0.4 * volatility_score(volatility_24h) +
        0.2 * momentum_score(momentum_24h)
    )


def build_live_screener() -> dict[str, object]:
    tickers = fetch_current_tickers()
    scored = []
    excluded = []

    for row in tickers:
        inst_id = row.get("instId", "")
        excluded_flag, reason = _is_excluded_symbol(inst_id)
        if excluded_flag:
            excluded.append({"instId": inst_id, "reason": reason})
            continue

        try:
            dollar_vol_24h = float(row.get("volCcy24h", 0.0))
            last = float(row.get("last", 0.0))
            open24h = float(row.get("open24h", 0.0))
            high24h = float(row.get("high24h", 0.0))
            low24h = float(row.get("low24h", 0.0))
        except (TypeError, ValueError):
            excluded.append({"instId": inst_id, "reason": "bad_numeric"})
            continue

        if open24h <= 0:
            excluded.append({"instId": inst_id, "reason": "bad_open24h"})
            continue

        volatility_24h = (high24h - low24h) / open24h * 100.0
        momentum_24h = (last - open24h) / open24h * 100.0

        if dollar_vol_24h < MIN_DOLLAR_VOL_24H:
            excluded.append({"instId": inst_id, "reason": "dollar_vol_lt_10m"})
            continue
        if volatility_24h > MAX_VOLATILITY_24H:
            excluded.append({"instId": inst_id, "reason": "volatility_gt_50pct"})
            continue

        scored.append(
            {
                "instId": inst_id,
                "score": compute_score(dollar_vol_24h, volatility_24h, momentum_24h),
                "vol24h_usd": dollar_vol_24h,
                "vol_pct": volatility_24h,
                "mom_pct": momentum_24h,
            }
        )

    scored = sorted(scored, key=lambda item: item["score"], reverse=True)
    tier2 = [item["instId"] for item in scored if item["instId"] not in TIER1][:TOP_N]
    tier3 = [item["instId"] for item in scored if item["instId"] not in TIER1][TOP_N:TOP_N + WATCH_N]

    output = {
        "updated_at": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tier1": TIER1,
        "tier2": tier2,
        "tier3": tier3,
        "excluded": excluded,
        "all_scored": scored,
    }
    LATEST_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(output, ensure_ascii=False) + "\n")
    return output


def screener_at_time(dt: pd.Timestamp, frames: dict[str, pd.DataFrame], ctvals: dict[str, float]) -> dict[str, object]:
    scored = []
    excluded = []

    for symbol, df in frames.items():
        excluded_flag, reason = _is_excluded_symbol(symbol)
        if excluded_flag:
            excluded.append({"instId": symbol, "reason": reason})
            continue

        hist = df.loc[df.index < dt].tail(1440)
        if len(hist) < 60:
            excluded.append({"instId": symbol, "reason": "insufficient_history"})
            continue

        open24h = float(hist.iloc[0]["open"])
        last = float(hist.iloc[-1]["close"])
        high24h = float(hist["high"].max())
        low24h = float(hist["low"].min())
        ct_val = ctvals.get(symbol, 1.0)
        dollar_vol_24h = float((hist["close"] * hist["vol"] * ct_val).sum())
        volatility_24h = (high24h - low24h) / open24h * 100.0 if open24h else 0.0
        momentum_24h = (last - open24h) / open24h * 100.0 if open24h else 0.0

        if dollar_vol_24h < MIN_DOLLAR_VOL_24H:
            excluded.append({"instId": symbol, "reason": "dollar_vol_lt_10m"})
            continue
        if volatility_24h > MAX_VOLATILITY_24H:
            excluded.append({"instId": symbol, "reason": "volatility_gt_50pct"})
            continue

        scored.append(
            {
                "instId": symbol,
                "score": compute_score(dollar_vol_24h, volatility_24h, momentum_24h),
                "vol24h_usd": dollar_vol_24h,
                "vol_pct": volatility_24h,
                "mom_pct": momentum_24h,
            }
        )

    scored = sorted(scored, key=lambda item: item["score"], reverse=True)
    tier2 = [item["instId"] for item in scored if item["instId"] not in TIER1][:TOP_N]
    tier3 = [item["instId"] for item in scored if item["instId"] not in TIER1][TOP_N:TOP_N + WATCH_N]
    return {
        "updated_at": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tier1": TIER1,
        "tier2": tier2,
        "tier3": tier3,
        "excluded": excluded,
        "all_scored": scored,
    }


def backtest_dynamic_vs_static(frames: dict[str, pd.DataFrame], ctvals: dict[str, float]) -> dict[str, object]:
    rows = []
    for symbol, df in frames.items():
        signals = detect_signals(df, BASE_VOL_MULT, BASE_PRICE_PCT, BASE_LOOKBACK, sym=symbol)
        for signal in signals:
            try:
                forward = analyze_forward(df, signal)
            except ValueError:
                continue
            screen = screener_at_time(signal.ts, frames, ctvals)
            active = set(screen["tier1"]) | set(screen["tier2"])
            rows.append(
                {
                    "sym": symbol,
                    "ts": signal.ts,
                    "direction": signal.direction,
                    "ret_10m": forward.directional_returns.get(10),
                    "selected_dynamic": symbol in active,
                }
            )

    backtest_df = pd.DataFrame(rows)
    dynamic = backtest_df[backtest_df["selected_dynamic"]]
    static = backtest_df.copy()

    lab_threshold = {}
    lab_df = frames.get("LAB-USDT-SWAP")
    if lab_df is not None:
        thresholds = [5_000_000.0, 10_000_000.0, 20_000_000.0, 50_000_000.0]
        for threshold in thresholds:
            days = 0
            excluded_days = 0
            for dt in lab_df.index[1440::1440]:
                hist = lab_df.loc[lab_df.index < dt].tail(1440)
                if len(hist) < 1440:
                    continue
                days += 1
                dv = float((hist["close"] * hist["vol"] * ctvals.get("LAB-USDT-SWAP", 1.0)).sum())
                if dv < threshold:
                    excluded_days += 1
            lab_threshold[str(int(threshold))] = {
                "days": days,
                "excluded_days": excluded_days,
                "excluded_pct": float(excluded_days / days * 100.0) if days else float("nan"),
            }

    summary = {
        "static_n": int(len(static)),
        "static_precision_10m": precision(static["ret_10m"]),
        "static_avg_ret_10m": mean(static["ret_10m"]),
        "dynamic_n": int(len(dynamic)),
        "dynamic_precision_10m": precision(dynamic["ret_10m"]),
        "dynamic_avg_ret_10m": mean(dynamic["ret_10m"]),
        "lab_thresholds": lab_threshold,
    }
    BACKTEST_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-live", action="store_true")
    args = parser.parse_args()

    frames = load_frames()
    ctvals = fetch_ctvals()
    if not args.skip_live:
        live = build_live_screener()
        print("=== LIVE SCREENER ===")
        print(f"tier1: {live['tier1']}")
        print(f"tier2: {live['tier2']}")
        print(f"tier3: {live['tier3']}")
        print(f"excluded_count: {len(live['excluded'])}")

    summary = backtest_dynamic_vs_static(frames, ctvals)
    print("\n=== BACKTEST DYNAMIC VS STATIC ===")
    for key, value in summary.items():
        print(f"{key}: {value}")

    print(f"\nSaved latest screener -> {LATEST_PATH}")
    print(f"Saved history log     -> {HISTORY_PATH}")
    print(f"Saved backtest        -> {BACKTEST_PATH}")


if __name__ == "__main__":
    main()
