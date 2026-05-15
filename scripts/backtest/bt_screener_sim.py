from __future__ import annotations

import argparse
import bisect
import copy
import json
import math
import os
import pickle
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import numpy as np
import yaml


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.strategy import signal_engine  # noqa: E402
from src.strategy.signal_engine import compute_signal  # noqa: E402


CACHE_DIR = ROOT / "scripts" / "backtest" / "cache" / "screener"
RESULTS_DIR = ROOT / "scripts" / "backtest" / "results"
CONFIG_PATH = ROOT / "config.yaml"
MARKET_PULSE_PATH = ROOT / "scripts" / "ws" / "cache" / "market_pulse.json"
SIM_LOG_PATH = ROOT / "scripts" / "backtest" / "bt_screener_sim.log"

TIMEFRAMES = ["5m", "15m", "1H", "4H"]
BAR_MS = {"5m": 5 * 60 * 1000, "15m": 15 * 60 * 1000, "1H": 60 * 60 * 1000, "4H": 4 * 60 * 60 * 1000}
DAY_MS = 24 * 60 * 60 * 1000
CALIBRATION_DAYS = 30
MIN_CALIBRATION_15M_CANDLES = 200
BILL_EXCLUDED = "BILL-USDT-SWAP"
SHORT_TEST_PAIR = "BASED-USDT-SWAP"
DYNAMIC_FORCE_FALLBACK = {"AI-USDT-SWAP", "UB-USDT-SWAP"}

RUNS = [
    {"id": 1, "params": "hardcode", "tf_signal": "15m"},
    {"id": 2, "params": "hardcode", "tf_signal": "5m"},
    {"id": 3, "params": "sweep", "tf_signal": "15m"},
    {"id": 4, "params": "sweep", "tf_signal": "5m"},
    {"id": 5, "params": "dynamic", "tf_signal": "15m"},
    {"id": 6, "params": "dynamic", "tf_signal": "5m"},
    {"id": 7, "params": "dynamic_cvd", "tf_signal": "15m"},
    {"id": 8, "params": "dynamic_cvd", "tf_signal": "5m"},
]

SWEEP_ADX = [15, 18, 20, 22, 25]
SWEEP_VOL_RATIO = [0.7, 1.0, 1.3, 1.5, 2.0]
SWEEP_BB_WIDTH = [0.3, 0.5, 0.8, 1.0]
TS_CACHE: dict[int, list[int]] = {}
DYNAMIC_ADX_PERCENTILE = float(os.environ.get("BT_DYNAMIC_ADX_PERCENTILE", "70"))
DYNAMIC_VOL_PERCENTILE = float(os.environ.get("BT_DYNAMIC_VOL_PERCENTILE", "75"))


@dataclass(frozen=True)
class Periods:
    first_ts: int
    last_ts: int
    calibration_start: int
    calibration_end: int
    test_start: int
    test_end: int
    short_test_period: bool


def ts_iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def base_symbol(inst_id: str) -> str:
    return inst_id.removesuffix("-SWAP")


def result_path(run: dict[str, Any]) -> Path:
    return RESULTS_DIR / f"screener_run_{run['id']}_{run['params']}_{run['tf_signal']}.jsonl"


def load_strategy_config() -> dict[str, Any]:
    payload = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return dict(payload.get("strategy") or {})


def load_cooldown_minutes() -> int:
    payload = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    main_cfg = payload.get("main_screener") or {}
    return int(main_cfg.get("cooldown_minutes", 60))


def load_cache() -> dict[str, dict[str, list[list[float | int]]]]:
    data: dict[str, dict[str, list[list[float | int]]]] = {}
    for path in sorted(CACHE_DIR.glob("*_60d.pkl")):
        stem = path.stem
        if not stem.endswith("_60d"):
            continue
        name = stem[:-4]
        try:
            inst_id, tf = name.rsplit("_", 1)
        except ValueError:
            continue
        if tf not in TIMEFRAMES:
            continue
        with path.open("rb") as fh:
            candles = pickle.load(fh)
        if isinstance(candles, list) and candles:
            data.setdefault(inst_id, {})[tf] = candles
    return {inst: tfs for inst, tfs in data.items() if all(tfs.get(tf) for tf in TIMEFRAMES)}


def candle_span_days(candles: list[list[float | int]]) -> float:
    if not candles:
        return 0.0
    return (int(candles[-1][0]) - int(candles[0][0])) / DAY_MS


def is_full_depth(pair_data: dict[str, list[list[float | int]]]) -> bool:
    return all(candle_span_days(pair_data[tf]) >= 59.25 for tf in TIMEFRAMES)


def periods_for_pair(inst_id: str, pair_data: dict[str, list[list[float | int]]]) -> Periods:
    first_ts = max(int(pair_data[tf][0][0]) for tf in TIMEFRAMES)
    last_ts = min(int(pair_data[tf][-1][0]) for tf in TIMEFRAMES)
    calibration_start = first_ts
    calibration_end = min(first_ts + CALIBRATION_DAYS * DAY_MS, last_ts)
    test_start = calibration_end
    test_end = last_ts
    short_test = inst_id == SHORT_TEST_PAIR

    if inst_id in DYNAMIC_FORCE_FALLBACK:
        # User rule: hardcode/sweep use the short history as-is; dynamic uses global fallback.
        test_start = first_ts
        test_end = last_ts
    return Periods(first_ts, last_ts, calibration_start, calibration_end, test_start, test_end, short_test)


def timestamps(candles: list[list[float | int]]) -> list[int]:
    key = id(candles)
    cached = TS_CACHE.get(key)
    if cached is None:
        cached = [int(row[0]) for row in candles]
        TS_CACHE[key] = cached
    return cached


def visible_newest(
    candles_oldest: list[list[float | int]],
    ts_ms: int,
    lookback: int,
) -> list[list[float | int]]:
    idx = bisect.bisect_right(timestamps(candles_oldest), ts_ms)
    if idx <= 0:
        return []
    return list(reversed(candles_oldest[max(0, idx - lookback):idx]))


def signal_times(pair_data: dict[str, list[list[float | int]]], tf_signal: str, start: int, end: int) -> Iterable[int]:
    candles = pair_data[tf_signal]
    for row in candles:
        ts_ms = int(row[0])
        if start <= ts_ms < end:
            yield ts_ms


def next_15m_candles(pair_data: dict[str, list[list[float | int]]], ts_ms: int, hold_min: int) -> list[list[float | int]]:
    c15 = pair_data["15m"]
    idx = bisect.bisect_right(timestamps(c15), ts_ms)
    max_bars = max(1, math.ceil(hold_min / 15))
    return c15[idx:idx + max_bars]


def simulate_forward(
    forward_15m: list[list[float | int]],
    side: str,
    entry: float,
    sl: float,
    tp1: float,
    hold_min: int,
) -> dict[str, Any]:
    if not forward_15m:
        return {"outcome": "EXPIRE", "pnl_pct": 0.0, "hold_actual_min": 0}

    # Backtest exits use TP1 only. TP2 may be emitted for reference, but never decides outcome.
    tp1_pct = abs(tp1 - entry) / entry * 100.0
    sl_pct = abs(entry - sl) / entry * 100.0
    expiry_close = float(forward_15m[-1][4])

    for idx, candle in enumerate(forward_15m, start=1):
        high = float(candle[2])
        low = float(candle[3])
        hold_actual = min(idx * 15, hold_min)
        if side == "buy":
            sl_hit = low <= sl
            tp_hit = high >= tp1
        else:
            sl_hit = high >= sl
            tp_hit = low <= tp1

        # OHLC cannot prove intra-candle ordering; choose SL first when both levels are inside one candle.
        if sl_hit:
            return {"outcome": "SL", "pnl_pct": -sl_pct, "hold_actual_min": hold_actual}
        if tp_hit:
            return {"outcome": "TP1", "pnl_pct": tp1_pct, "hold_actual_min": hold_actual}

    if side == "buy":
        pnl_pct = (expiry_close - entry) / entry * 100.0
    else:
        pnl_pct = (entry - expiry_close) / entry * 100.0
    return {"outcome": "EXPIRE", "pnl_pct": pnl_pct, "hold_actual_min": min(len(forward_15m) * 15, hold_min)}


def metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
    wins = [row["pnl_pct"] for row in rows if float(row.get("pnl_pct", 0.0)) > 0]
    losses = [row["pnl_pct"] for row in rows if float(row.get("pnl_pct", 0.0)) < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "signals": float(len(rows)),
        "wr": (len(wins) / len(rows) * 100.0) if rows else 0.0,
        "pf": (gross_win / gross_loss) if gross_loss else (99.0 if gross_win else 0.0),
    }


def make_threshold_params(symbol: str, adx: float, vol_ratio: float, bb_width: float | None) -> dict[str, Any]:
    params = copy.deepcopy(signal_engine._PAIR_PARAMS.get(symbol, signal_engine._PAIR_PARAMS_DEFAULT))
    params.setdefault("allowed_modes", ["FAST", "SWING"])
    regimes = params.setdefault("regimes", {})
    trending = regimes.setdefault("trending", {})
    for style in ("fast", "swing"):
        cfg = trending.setdefault(style, {})
        cfg["adx"] = float(adx)
        cfg["vol"] = float(vol_ratio)
        if bb_width is not None:
            cfg["bb_width_min"] = float(bb_width)
    drift = regimes.setdefault("drift", {}).setdefault("fast", {})
    drift["vol"] = float(vol_ratio)
    ranging = regimes.setdefault("ranging", {}).setdefault("fast", {})
    ranging["vol"] = float(vol_ratio)
    if bb_width is not None:
        ranging["bb_width_min"] = float(bb_width)
    return params


@contextmanager
def patched_pair_params(symbol: str, params: dict[str, Any] | None):
    if params is None:
        yield
        return
    old_params = signal_engine._PAIR_PARAMS
    try:
        patched = copy.deepcopy(old_params)
        patched[symbol] = params
        signal_engine._PAIR_PARAMS = patched
        yield
    finally:
        signal_engine._PAIR_PARAMS = old_params


def call_signal(
    pair_data: dict[str, list[list[float | int]]],
    symbol: str,
    ts_ms: int,
    strategy_config: dict[str, Any],
    pair_params: dict[str, Any] | None = None,
):
    candles_15m = visible_newest(pair_data["15m"], ts_ms, 160)
    candles_1h = visible_newest(pair_data["1H"], ts_ms, 120)
    candles_4h = visible_newest(pair_data["4H"], ts_ms, 90)
    candles_5m = visible_newest(pair_data["5m"], ts_ms, 240)
    if len(candles_15m) < 60 or len(candles_1h) < 50 or len(candles_4h) < 30 or len(candles_5m) < 50:
        return None
    with patched_pair_params(symbol, pair_params):
        try:
            return compute_signal(
                candles_15m=candles_15m,
                candles_1h=candles_1h,
                candles_4h=candles_4h,
                candles_5m=candles_5m,
                symbol=symbol,
                config=strategy_config,
                captured_at_iso=ts_iso(ts_ms),
            )
        except Exception as exc:
            SIM_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with SIM_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(f"{ts_iso(ts_ms)} {symbol} compute_signal skipped: {type(exc).__name__}: {exc}\n")
            return None


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    return float(np.percentile(np.array(values, dtype=float), q))


def compute_profile(
    inst_id: str,
    pair_data: dict[str, list[list[float | int]]],
    strategy_config: dict[str, Any],
    start_ts: int,
    end_ts: int,
) -> dict[str, float] | None:
    c15_count = sum(1 for row in pair_data["15m"] if start_ts <= int(row[0]) < end_ts)
    if c15_count < MIN_CALIBRATION_15M_CANDLES:
        return None

    symbol = base_symbol(inst_id)
    adx_values: list[float] = []
    vol_values: list[float] = []
    bb_values: list[float] = []
    atr_pct_values: list[float] = []

    for idx, ts_ms in enumerate(signal_times(pair_data, "15m", start_ts, end_ts)):
        if idx % 4:
            continue
        result = call_signal(pair_data, symbol, ts_ms, strategy_config)
        if result is None:
            continue
        adx_values.append(float(result.context.get("adx_1h") or 0.0))
        vol_values.append(float(result.context.get("vol_ratio_sig") or 0.0))
        bb_values.append(float(result.indicators.get("15m", {}).get("bb_width_pct") or 0.0))
        atr_pct_values.append(float(result.indicators.get("15m", {}).get("atr_pct") or 0.0))

    if not adx_values:
        return None
    return {
        "adx_p70": percentile(adx_values, DYNAMIC_ADX_PERCENTILE),
        "vol_ratio_p75": percentile(vol_values, DYNAMIC_VOL_PERCENTILE),
        "bb_width_p50": percentile(bb_values, 50),
        "atr_pct_p50": percentile(atr_pct_values, 50),
    }


def global_fallback_profile(
    data: dict[str, dict[str, list[list[float | int]]]],
    strategy_config: dict[str, Any],
) -> dict[str, float]:
    profiles: list[dict[str, float]] = []
    for inst_id, pair_data in data.items():
        if inst_id == BILL_EXCLUDED or not is_full_depth(pair_data):
            continue
        periods = periods_for_pair(inst_id, pair_data)
        profile = compute_profile(inst_id, pair_data, strategy_config, periods.calibration_start, periods.calibration_end)
        if profile:
            profiles.append(profile)
    if not profiles:
        return {"adx_p70": 20.0, "vol_ratio_p75": 1.3, "bb_width_p50": 0.8, "atr_pct_p50": 50.0}
    keys = profiles[0].keys()
    return {key: float(median(profile[key] for profile in profiles)) for key in keys}


def dynamic_profile_for_ts(
    inst_id: str,
    pair_data: dict[str, list[list[float | int]]],
    strategy_config: dict[str, Any],
    periods: Periods,
    ts_ms: int,
    fallback: dict[str, float],
    cache: dict[tuple[str, int], dict[str, float]],
) -> tuple[dict[str, float], bool]:
    if inst_id in DYNAMIC_FORCE_FALLBACK:
        return fallback, True

    bucket = max(0, (ts_ms - periods.test_start) // (7 * DAY_MS))
    window_end = periods.test_start + bucket * 7 * DAY_MS
    window_start = max(periods.first_ts, window_end - CALIBRATION_DAYS * DAY_MS)
    key = (inst_id, window_end)
    if key not in cache:
        profile = compute_profile(inst_id, pair_data, strategy_config, window_start, window_end)
        cache[key] = profile or fallback
    return cache[key], cache[key] == fallback


def load_market_pulse() -> Any:
    if not MARKET_PULSE_PATH.exists():
        return None
    try:
        return json.loads(MARKET_PULSE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def cvd_taker_ratio(market_pulse: Any, inst_id: str, ts_ms: int) -> float | None:
    if not market_pulse:
        return None
    candidates: list[dict[str, Any]] = []
    if isinstance(market_pulse, list):
        candidates = [row for row in market_pulse if isinstance(row, dict)]
    elif isinstance(market_pulse, dict):
        symbol_payload = market_pulse.get(inst_id) or market_pulse.get(base_symbol(inst_id))
        if isinstance(symbol_payload, list):
            candidates = [row for row in symbol_payload if isinstance(row, dict)]
        elif isinstance(symbol_payload, dict):
            candidates = [symbol_payload]
        elif "taker_ratio" in market_pulse:
            candidates = [market_pulse]

    best_ratio: float | None = None
    best_ts = -1
    for row in candidates:
        raw_ts = row.get("ts") or row.get("ts_ms") or row.get("timestamp") or row.get("updated_at")
        try:
            row_ts = int(raw_ts) if isinstance(raw_ts, (int, float, str)) and str(raw_ts).isdigit() else int(
                datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00")).timestamp() * 1000
            )
        except Exception:
            row_ts = ts_ms
        ratio = row.get("taker_ratio")
        if ratio is None:
            ratio = row.get("buy_ratio")
        try:
            ratio_f = float(ratio)
        except (TypeError, ValueError):
            continue
        if row_ts <= ts_ms and row_ts > best_ts and ts_ms - row_ts <= 30 * 60 * 1000:
            best_ts = row_ts
            best_ratio = ratio_f
    return best_ratio


def passes_cvd_filter(market_pulse: Any, inst_id: str, ts_ms: int, side: str) -> bool:
    ratio = cvd_taker_ratio(market_pulse, inst_id, ts_ms)
    if ratio is None:
        return True
    if side == "buy":
        return ratio > 0.55
    if side == "sell":
        return ratio < 0.45
    return True


def build_trade_row(
    run: dict[str, Any],
    inst_id: str,
    ts_ms: int,
    result: Any,
    outcome: dict[str, Any],
    short_test_period: bool,
    dynamic_fallback: bool,
) -> dict[str, Any]:
    entry = float(result.entry_price)
    sl = float(result.sl_price)
    tp1 = float(result.tp1_price)
    tp2 = float(result.tp2_price) if result.tp2_price is not None else None
    return {
        "run_id": run["id"],
        "params_type": run["params"],
        "tf_signal": run["tf_signal"],
        "symbol": base_symbol(inst_id),
        "ts": ts_iso(ts_ms),
        "regime": result.regime,
        "trade_style": result.trade_style,
        "side": result.side,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "hold_min": int(result.max_hold_min),
        "outcome": outcome["outcome"],
        "pnl_pct": round(float(outcome["pnl_pct"]), 4),
        "hold_actual_min": int(outcome["hold_actual_min"]),
        "adx_at_entry": round(float(result.context.get("adx_1h") or 0.0), 2),
        "vol_ratio_at_entry": round(float(result.context.get("vol_ratio_sig") or 0.0), 3),
        "bb_width_at_entry": round(float(result.indicators.get("15m", {}).get("bb_width_pct") or 0.0), 3),
        "short_test_period": bool(short_test_period),
        "dynamic_fallback": bool(dynamic_fallback),
    }


def simulate_run(
    run: dict[str, Any],
    data: dict[str, dict[str, list[list[float | int]]]],
    strategy_config: dict[str, Any],
    *,
    pairs: set[str] | None = None,
    write: bool = True,
    max_signals: int | None = None,
    period_kind: str = "test",
    sweep_params: dict[str, float] | None = None,
    dynamic_fallback_profile_data: dict[str, float] | None = None,
    cooldown_minutes: int = 60,
    quiet: bool = False,
    output_path: Path | None = None,
) -> list[dict[str, Any]]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    out_fh = (output_path or result_path(run)).open("w", encoding="utf-8") if write else None
    market_pulse = load_market_pulse() if run["params"] == "dynamic_cvd" else None
    dynamic_cache: dict[tuple[str, int], dict[str, float]] = {}
    fallback_profile = dynamic_fallback_profile_data
    active_setup_ts: dict[str, int] = {}
    if run["params"] in {"dynamic", "dynamic_cvd"} and fallback_profile is None:
        fallback_profile = global_fallback_profile(data, strategy_config)

    try:
        pair_items = sorted(data.items())
        for pair_idx, (inst_id, pair_data) in enumerate(pair_items, start=1):
            if inst_id == BILL_EXCLUDED:
                continue
            if pairs and inst_id not in pairs and base_symbol(inst_id) not in pairs:
                continue

            periods = periods_for_pair(inst_id, pair_data)
            if period_kind == "calibration":
                start_ts, end_ts = periods.calibration_start, periods.calibration_end
            elif period_kind == "all_available":
                start_ts, end_ts = periods.first_ts, periods.last_ts
            elif inst_id in DYNAMIC_FORCE_FALLBACK and run["params"] in {"hardcode", "sweep"}:
                start_ts, end_ts = periods.first_ts, periods.last_ts
            else:
                start_ts, end_ts = periods.test_start, periods.test_end

            symbol = base_symbol(inst_id)
            for n_seen, ts_ms in enumerate(signal_times(pair_data, run["tf_signal"], start_ts, end_ts), start=1):
                pair_params = None
                dynamic_fallback = False

                if run["params"] == "sweep" and sweep_params:
                    pair_params = make_threshold_params(
                        symbol,
                        sweep_params["adx"],
                        sweep_params["vol_ratio"],
                        sweep_params["bb_width"],
                    )
                elif run["params"] in {"dynamic", "dynamic_cvd"}:
                    assert fallback_profile is not None
                    profile, dynamic_fallback = dynamic_profile_for_ts(
                        inst_id,
                        pair_data,
                        strategy_config,
                        periods,
                        ts_ms,
                        fallback_profile,
                        dynamic_cache,
                    )
                    pair_params = make_threshold_params(
                        symbol,
                        profile["adx_p70"],
                        profile["vol_ratio_p75"],
                        profile["bb_width_p50"],
                    )

                result = call_signal(pair_data, symbol, ts_ms, strategy_config, pair_params)
                if result is None or result.entry_signal != "ENTRY":
                    continue
                if not result.side or not result.entry_price or not result.sl_price or not result.tp1_price or not result.max_hold_min:
                    continue
                if run["params"] in {"dynamic", "dynamic_cvd"}:
                    profile = profile if "profile" in locals() else fallback_profile
                    atr_pct = float(result.indicators.get("15m", {}).get("atr_pct") or 0.0)
                    if atr_pct < float(profile["atr_pct_p50"]):
                        continue
                if run["params"] == "dynamic_cvd" and not passes_cvd_filter(market_pulse, inst_id, ts_ms, result.side):
                    continue

                cooldown_key = (
                    f"{inst_id}:{result.trade_style if result.trade_style == 'BB_FADE' else result.regime}:{result.side}"
                )
                last_signal_ts = active_setup_ts.get(cooldown_key)
                if last_signal_ts is not None and ts_ms - last_signal_ts < cooldown_minutes * 60 * 1000:
                    continue

                forward = next_15m_candles(pair_data, ts_ms, int(result.max_hold_min))
                if not forward:
                    continue
                outcome = simulate_forward(
                    forward,
                    result.side,
                    float(result.entry_price),
                    float(result.sl_price),
                    float(result.tp1_price),
                    int(result.max_hold_min),
                )
                row = build_trade_row(run, inst_id, ts_ms, result, outcome, periods.short_test_period, dynamic_fallback)
                active_setup_ts[cooldown_key] = ts_ms
                rows.append(row)
                if out_fh:
                    out_fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    out_fh.flush()

                if not quiet and max_signals is not None:
                    print(
                        f"Run {run['id']}/8 [{run['params']} {run['tf_signal']}]: "
                        f"{inst_id} {n_seen}... signal #{len(rows)}"
                    )
                if max_signals is not None and len(rows) >= max_signals:
                    return rows

        m = metrics(rows)
        if not quiet:
            print(f"Run {run['id']} done - signals={int(m['signals'])}, WR={m['wr']:.1f}%, PF={m['pf']:.2f}")
        return rows
    finally:
        if out_fh:
            out_fh.close()


def optimize_sweep(
    run: dict[str, Any],
    data: dict[str, dict[str, list[list[float | int]]]],
    strategy_config: dict[str, Any],
    cooldown_minutes: int,
) -> dict[str, float]:
    permissive = {
        "adx": float(min(SWEEP_ADX)),
        "vol_ratio": float(min(SWEEP_VOL_RATIO)),
        "bb_width": float(min(SWEEP_BB_WIDTH)),
    }
    calib_pairs = {inst_id for inst_id, pair_data in data.items() if inst_id != BILL_EXCLUDED and is_full_depth(pair_data)}
    calibration_rows = simulate_run(
        run,
        data,
        strategy_config,
        pairs=calib_pairs,
        write=False,
        period_kind="calibration",
        sweep_params=permissive,
        cooldown_minutes=cooldown_minutes,
        quiet=True,
    )
    best: dict[str, float] | None = None
    best_pf = -1.0
    for adx in SWEEP_ADX:
        for vol_ratio in SWEEP_VOL_RATIO:
            for bb_width in SWEEP_BB_WIDTH:
                rows = [
                    row
                    for row in calibration_rows
                    if float(row.get("adx_at_entry") or 0.0) >= adx
                    and float(row.get("vol_ratio_at_entry") or 0.0) >= vol_ratio
                    and float(row.get("bb_width_at_entry") or 0.0) >= bb_width
                ]
                pf = metrics(rows)["pf"]
                if pf > best_pf:
                    best_pf = pf
                    best = {"adx": float(adx), "vol_ratio": float(vol_ratio), "bb_width": float(bb_width)}
    return best or {"adx": 20.0, "vol_ratio": 1.3, "bb_width": 0.8}


def selected_inst_ids(
    data: dict[str, dict[str, list[list[float | int]]]],
    pair_filter: set[str] | None,
) -> list[str]:
    if not pair_filter:
        return sorted(data)
    selected: list[str] = []
    for inst_id in sorted(data):
        if inst_id in pair_filter or base_symbol(inst_id) in pair_filter:
            selected.append(inst_id)
    return selected


def default_fallback_profile() -> dict[str, float]:
    return {"adx_p70": 20.0, "vol_ratio_p75": 1.3, "bb_width_p50": 0.8, "atr_pct_p50": 50.0}


def print_dynamic_profiles(
    data: dict[str, dict[str, list[list[float | int]]]],
    strategy_config: dict[str, Any],
    pair_filter: set[str] | None,
    fallback_profile: dict[str, float] | None,
) -> None:
    for inst_id in selected_inst_ids(data, pair_filter):
        if inst_id == BILL_EXCLUDED:
            continue
        pair_data = data[inst_id]
        periods = periods_for_pair(inst_id, pair_data)
        if inst_id in DYNAMIC_FORCE_FALLBACK:
            profile = fallback_profile or global_fallback_profile(data, strategy_config)
            source = "global_fallback"
        else:
            profile = compute_profile(
                inst_id,
                pair_data,
                strategy_config,
                periods.calibration_start,
                periods.calibration_end,
            )
            if profile is None:
                profile = fallback_profile or global_fallback_profile(data, strategy_config)
                source = "global_fallback"
            else:
                source = "calibration"
        print(
            f"Dynamic profile {inst_id} ({source}): "
            f"adx_p70={profile['adx_p70']:.2f}, "
            f"vol_ratio_p75={profile['vol_ratio_p75']:.3f}, "
            f"bb_width_p50={profile['bb_width_p50']:.3f}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run screener backtests against cached OKX candles.")
    parser.add_argument("--run-id", type=int, choices=[run["id"] for run in RUNS], help="Run only one strategy variant.")
    parser.add_argument("--pairs", nargs="*", help="Restrict to instId/base symbols, e.g. BTC-USDT-SWAP BTC-USDT.")
    parser.add_argument("--max-signals", type=int, help="Stop after N written signals.")
    parser.add_argument("--period", choices=["test", "calibration", "all_available"], default="test")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = load_cache()
    if not data:
        raise SystemExit(f"No screener cache found in {CACHE_DIR}")

    strategy_config = load_strategy_config()
    cooldown_minutes = load_cooldown_minutes()
    selected_runs = [run for run in RUNS if args.run_id in (None, run["id"])]
    pair_filter = set(args.pairs) if args.pairs else None
    fallback_profile: dict[str, float] | None = None

    for run in selected_runs:
        sweep_params = None
        if run["params"] == "sweep":
            sweep_params = optimize_sweep(run, data, strategy_config, cooldown_minutes)
            print(f"Run {run['id']}/8 sweep selected: {sweep_params}")
        if (
            run["params"] in {"dynamic", "dynamic_cvd"}
            and fallback_profile is None
            and (pair_filter is None or any(inst_id in DYNAMIC_FORCE_FALLBACK for inst_id in selected_inst_ids(data, pair_filter)))
        ):
            fallback_profile = global_fallback_profile(data, strategy_config)
            print(f"Dynamic global fallback profile: {fallback_profile}")
        elif run["params"] in {"dynamic", "dynamic_cvd"} and fallback_profile is None:
            fallback_profile = default_fallback_profile()
        if run["params"] in {"dynamic", "dynamic_cvd"}:
            print_dynamic_profiles(data, strategy_config, pair_filter, fallback_profile)

        rows = simulate_run(
            run,
            data,
            strategy_config,
            pairs=pair_filter,
            write=True,
            max_signals=args.max_signals,
            period_kind=args.period,
            sweep_params=sweep_params,
            dynamic_fallback_profile_data=fallback_profile,
            cooldown_minutes=cooldown_minutes,
        )
        if args.max_signals is not None:
            m = metrics(rows)
            print(f"Run {run['id']} done - signals={int(m['signals'])}, WR={m['wr']:.1f}%, PF={m['pf']:.2f}")
        if args.max_signals:
            print("\nFirst signals:")
            for row in rows[: args.max_signals]:
                print(
                    f"{row['ts']} {row['symbol']} {row['side']} "
                    f"entry={row['entry']} sl={row['sl']} tp1={row['tp1']} "
                    f"outcome={row['outcome']} pnl={row['pnl_pct']:+.3f}%"
                )


if __name__ == "__main__":
    main()
