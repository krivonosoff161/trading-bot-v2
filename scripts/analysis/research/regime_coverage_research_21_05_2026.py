from __future__ import annotations

import json
import math
import pickle
import sys
import time
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as patches
import matplotlib.pyplot as plt
import requests
import yaml


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategy.signal_engine import compute_signal  # noqa: E402


SUFFIX = "21_05_2026"
SIGNALS_PATH = ROOT / "logs" / "signals" / "main_signals.jsonl"
LABELS_PATH = ROOT / "logs" / "signals" / "main_signals_labels.jsonl"
UNIVERSE_PATH = ROOT / "scripts" / "ws" / "cache" / "main_universe.json"
CACHE_DIR = ROOT / "scripts" / "backtest" / "cache" / "screener"
OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"
CASE_DIR = OUT_DIR / f"regime_coverage_missed_cases_{SUFFIX}"
REPORT_MD = OUT_DIR / f"regime_coverage_report_{SUFFIX}.md"
SUMMARY_JSON = OUT_DIR / f"regime_coverage_summary_{SUFFIX}.json"
RUN_LOG = OUT_DIR / f"regime_coverage_run_{SUFFIX}.log"

CONFIG = {
    "analysis_days": 10,
    "fetch_missing_from_okx": True,
    "okx_sleep_sec": 0.08,
    "okx_limit": 300,
    "min_replay_15m": 70,
    "min_replay_1h": 60,
    "min_replay_4h": 55,
    "min_replay_5m": 90,
    "slice_15m": 120,
    "slice_1h": 100,
    "slice_4h": 80,
    "slice_5m": 180,
    "fast_horizon_15m": 4,
    "swing_horizon_15m": 16,
    "fast_min_move_pct": 0.80,
    "swing_min_move_pct": 1.40,
    "atr_mult": 1.15,
    "max_adverse_ratio": 0.75,
    "event_cooldown_15m": 6,
    "case_limit_per_regime": 8,
}

TF_MS = {
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "1H": 60 * 60 * 1000,
    "4H": 4 * 60 * 60 * 1000,
}


@dataclass(slots=True)
class CandleSet:
    rows: dict[str, list[list[Any]]]
    ts: dict[str, list[int]]
    source: str


def log(message: str) -> None:
    print(message)
    with RUN_LOG.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def safe_float(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out if math.isfinite(out) else float("nan")


def average(values: Iterable[float]) -> float:
    vals = [v for v in values if math.isfinite(v)]
    return sum(vals) / len(vals) if vals else float("nan")


def percentile(values: Iterable[float], q: float) -> float:
    vals = sorted(v for v in values if math.isfinite(v))
    if not vals:
        return float("nan")
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q / 100
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return vals[lo]
    return vals[lo] * (hi - pos) + vals[hi] * (pos - lo)


def pct(part: int | float, total: int | float) -> float:
    return part / total * 100 if total else float("nan")


def fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(val):
        return "n/a"
    return f"{val:.2f}{suffix}"


def iso_from_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def ms_from_iso(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def normalize_rows(raw: list[list[Any]]) -> list[list[Any]]:
    rows = []
    seen = set()
    for row in raw:
        ts_ms = int(float(row[0]))
        if ts_ms in seen:
            continue
        seen.add(ts_ms)
        out = [ts_ms]
        for value in row[1:]:
            out.append(float(value) if isinstance(value, str) and value.replace(".", "", 1).replace("-", "", 1).isdigit() else value)
        rows.append(out)
    rows.sort(key=lambda r: int(r[0]))
    return rows


def load_pickle_rows(path: Path) -> list[list[Any]]:
    if not path.exists():
        return []
    with path.open("rb") as fh:
        return normalize_rows(pickle.load(fh))


def fetch_okx_history(symbol: str, tf: str, start_ms: int, end_ms: int) -> list[list[Any]]:
    all_rows: list[list[Any]] = []
    cursor = end_ms + TF_MS[tf]
    before = max(0, start_ms - TF_MS[tf])
    while True:
        params = {
            "instId": symbol,
            "bar": tf,
            "limit": str(CONFIG["okx_limit"]),
            "after": str(cursor),
            "before": str(before),
        }
        response = requests.get(
            "https://www.okx.com/api/v5/market/history-candles",
            params=params,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "0":
            raise RuntimeError(f"OKX code={payload.get('code')} msg={payload.get('msg')}")
        batch = payload.get("data") or []
        if not batch:
            break
        all_rows.extend(batch)
        oldest = min(int(float(row[0])) for row in batch)
        if oldest <= start_ms or len(batch) < CONFIG["okx_limit"]:
            break
        cursor = oldest
        time.sleep(CONFIG["okx_sleep_sec"])
    time.sleep(CONFIG["okx_sleep_sec"])
    return normalize_rows(all_rows)


def row_close_ms(row: list[Any], tf: str) -> int:
    return int(row[0]) + TF_MS[tf]


def slice_newest_first(rows: list[list[Any]], ts_list: list[int], tf: str, close_cutoff_ms: int, limit: int) -> list[list[Any]]:
    max_open = close_cutoff_ms - TF_MS[tf]
    end = bisect_right(ts_list, max_open)
    start = max(0, end - limit)
    return list(reversed(rows[start:end]))


def load_strategy_config() -> dict[str, Any]:
    with (ROOT / "config.yaml").open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}
    return dict(payload.get("strategy") or {})


def load_universe() -> list[str]:
    if UNIVERSE_PATH.exists():
        payload = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
        pairs = payload.get("pairs") or []
        if pairs:
            return list(dict.fromkeys(pairs))
    return [
        "BTC-USDT-SWAP",
        "ETH-USDT-SWAP",
        "SOL-USDT-SWAP",
        "XRP-USDT-SWAP",
        "DOGE-USDT-SWAP",
        "ADA-USDT-SWAP",
    ]


def cached_reference_window(universe: list[str]) -> tuple[int, int]:
    latest = []
    for symbol in universe:
        path = CACHE_DIR / f"{symbol}_15m_60d.pkl"
        rows = load_pickle_rows(path)
        if rows:
            latest.append(int(rows[-1][0]))
    if not latest:
        raise RuntimeError("No local 15m cache found")
    end_open = min(latest)
    end_close = end_open + TF_MS["15m"]
    start_close = end_close - int(CONFIG["analysis_days"]) * 24 * 60 * 60 * 1000
    return start_close, end_close


def load_symbol_candles(symbol: str, start_close_ms: int, end_close_ms: int) -> CandleSet | None:
    rows: dict[str, list[list[Any]]] = {}
    source_parts = []
    warmup_start = start_close_ms - 12 * 24 * 60 * 60 * 1000
    for tf in ["5m", "15m", "1H", "4H"]:
        cached = load_pickle_rows(CACHE_DIR / f"{symbol}_{tf}_60d.pkl")
        sliced = [
            row for row in cached
            if warmup_start - TF_MS[tf] <= row_close_ms(row, tf) <= end_close_ms + TF_MS[tf]
        ]
        if sliced:
            rows[tf] = sliced
            source_parts.append(f"{tf}=cache")
            continue
        if not CONFIG["fetch_missing_from_okx"]:
            return None
        try:
            fetched = fetch_okx_history(symbol, tf, warmup_start, end_close_ms)
        except Exception as exc:
            log(f"{symbol} {tf}: OKX fetch failed: {exc}")
            return None
        if not fetched:
            return None
        rows[tf] = fetched
        source_parts.append(f"{tf}=okx")
    ts = {tf: [int(row[0]) for row in tf_rows] for tf, tf_rows in rows.items()}
    return CandleSet(rows=rows, ts=ts, source=",".join(source_parts))


def cheap_atr_pct(candles: list[list[Any]], idx: int, period: int = 14) -> float:
    start = max(1, idx - period + 1)
    vals = []
    for i in range(start, idx + 1):
        prev_close = float(candles[i - 1][4])
        high = float(candles[i][2])
        low = float(candles[i][3])
        vals.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    close = float(candles[idx][4])
    return average(vals) / close * 100 if close > 0 else float("nan")


def direction_side(direction: str) -> str:
    return "buy" if direction == "long" else "sell"


def detect_tradeable_moves(symbol: str, rows_15m: list[list[Any]], decisions_by_open: dict[int, dict[str, Any]], start_close_ms: int, end_close_ms: int) -> list[dict[str, Any]]:
    events = []
    i = CONFIG["min_replay_15m"]
    while i < len(rows_15m) - CONFIG["swing_horizon_15m"] - 1:
        candle = rows_15m[i]
        close_ms = row_close_ms(candle, "15m")
        if close_ms < start_close_ms or close_ms > end_close_ms:
            i += 1
            continue
        close = float(candle[4])
        future = rows_15m[i + 1 : i + 1 + CONFIG["swing_horizon_15m"]]
        if not future or close <= 0:
            i += 1
            continue
        up_idx, up_high = max(enumerate([float(row[2]) for row in future], start=1), key=lambda item: item[1])
        down_idx, down_low = min(enumerate([float(row[3]) for row in future], start=1), key=lambda item: item[1])
        up_pct = (up_high - close) / close * 100
        down_pct = (close - down_low) / close * 100
        if up_pct >= down_pct:
            direction = "long"
            move_pct = up_pct
            peak_bars = up_idx
            adverse = max(0.0, (close - min(float(row[3]) for row in future[:peak_bars])) / close * 100)
            peak_price = up_high
        else:
            direction = "short"
            move_pct = down_pct
            peak_bars = down_idx
            adverse = max(0.0, (max(float(row[2]) for row in future[:peak_bars]) - close) / close * 100)
            peak_price = down_low
        atr_pct = cheap_atr_pct(rows_15m, i)
        fast_threshold = max(CONFIG["fast_min_move_pct"], CONFIG["atr_mult"] * atr_pct)
        swing_threshold = max(CONFIG["swing_min_move_pct"], CONFIG["atr_mult"] * atr_pct)
        if peak_bars <= CONFIG["fast_horizon_15m"] and move_pct >= fast_threshold:
            move_type = "FAST"
        elif move_pct >= swing_threshold:
            move_type = "SWING"
        else:
            i += 1
            continue
        if move_pct <= 0 or adverse / move_pct > CONFIG["max_adverse_ratio"]:
            i += 1
            continue
        start_decision = decisions_by_open.get(int(candle[0]))
        if not start_decision:
            i += 1
            continue
        expected_side = direction_side(direction)
        nearby_decisions = []
        for offset in range(-2, 2):
            j = i + offset
            if 0 <= j < len(rows_15m):
                near = decisions_by_open.get(int(rows_15m[j][0]))
                if near:
                    nearby_decisions.append(near)
        matching_entry = next(
            (d for d in nearby_decisions if d["entry_signal"] == "ENTRY" and d.get("side") == expected_side),
            None,
        )
        wrong_entry = next((d for d in nearby_decisions if d["entry_signal"] == "ENTRY"), None)
        if matching_entry:
            decision = matching_entry
            entry_signal = "ENTRY"
            decision_side = matching_entry.get("side")
            coverage = "caught"
        elif wrong_entry:
            decision = wrong_entry
            entry_signal = "ENTRY"
            decision_side = wrong_entry.get("side")
            coverage = "entry_wrong_side"
        else:
            decision = start_decision
            entry_signal = start_decision["entry_signal"]
            decision_side = start_decision.get("side")
            coverage = "wait" if entry_signal == "WAIT" else "missed"
        events.append(
            {
                "symbol": symbol,
                "start_open_ms": int(candle[0]),
                "start_ts": iso_from_ms(close_ms),
                "entry_price": close,
                "direction": direction,
                "move_type": move_type,
                "move_pct": move_pct,
                "peak_bars_15m": peak_bars,
                "peak_price": peak_price,
                "adverse_pct": adverse,
                "atr_pct": atr_pct,
                "regime": decision["regime"],
                "engine_style": decision["trade_style"],
                "entry_signal": entry_signal,
                "engine_side": decision_side,
                "drop_reason": start_decision.get("drop_reason") or "",
                "diagnostic_reason": diagnostic_reason(start_decision, direction),
                "coverage": coverage,
                "engine": decision,
                "start_engine": start_decision,
            }
        )
        i += max(CONFIG["event_cooldown_15m"], peak_bars)
    return events


def diagnostic_reason(decision: dict[str, Any], direction: str) -> str:
    drop = decision.get("drop_reason") or ""
    trade_style = decision.get("trade_style")
    if drop and drop not in {"conditions_not_met", "missing_levels"}:
        return drop
    if drop == "missing_levels" and trade_style != "NO_TRADE":
        return drop
    ev = decision.get("engine_vars") or {}
    ind = decision.get("indicators") or {}
    side = direction_side(direction)
    vol = safe_float(ev.get("vol_ratio_sig"))
    slope_15m = safe_float(decision.get("slope_15m"))
    slope_1h = safe_float(decision.get("slope_1h"))
    adx_1h = safe_float(ev.get("adx_1h"))
    regime = decision.get("regime")
    bias_1h = ev.get("bias_1h")
    bb_width = safe_float((ind.get("15m") or {}).get("bb_width_pct"))
    day_pos = safe_float(ev.get("day_position"))
    if side == "buy" and bias_1h == "DOWN":
        return "conditions_not_met:bias_1h_down"
    if side == "sell" and bias_1h == "UP":
        return "conditions_not_met:bias_1h_up"
    if regime == "TRENDING" and not ev.get("adx_1h_rising"):
        return "conditions_not_met:adx_not_rising"
    if math.isfinite(vol) and vol < 1.0:
        return "conditions_not_met:low_vol_under_1"
    if math.isfinite(vol) and regime == "TRENDING" and vol < 1.5:
        return "conditions_not_met:low_trend_vol"
    if regime in {"TRENDING", "DRIFT"}:
        slope = slope_15m if decision.get("move_type_hint") != "SWING" else slope_1h
        if side == "buy" and math.isfinite(slope) and slope < 35:
            return "conditions_not_met:slope_not_up"
        if side == "sell" and math.isfinite(slope) and slope > -35:
            return "conditions_not_met:slope_not_down"
    if regime == "RANGING":
        if not math.isfinite(day_pos):
            return "conditions_not_met:no_day_position"
        if math.isfinite(bb_width) and not (0.8 <= bb_width <= 2.5):
            return "conditions_not_met:bb_width_outside_corridor"
        if math.isfinite(adx_1h) and adx_1h > 28:
            return "conditions_not_met:ranging_adx_too_high"
    return "conditions_not_met:composite"


def replay_symbol(symbol: str, candle_set: CandleSet, strategy_cfg: dict[str, Any], start_close_ms: int, end_close_ms: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    decisions = []
    decisions_by_open = {}
    rows_15m = candle_set.rows["15m"]
    for idx, candle in enumerate(rows_15m):
        close_ms = row_close_ms(candle, "15m")
        if close_ms < start_close_ms or close_ms > end_close_ms:
            continue
        if idx < CONFIG["min_replay_15m"]:
            continue
        raw_15m = slice_newest_first(candle_set.rows["15m"], candle_set.ts["15m"], "15m", close_ms, CONFIG["slice_15m"])
        raw_1h = slice_newest_first(candle_set.rows["1H"], candle_set.ts["1H"], "1H", close_ms, CONFIG["slice_1h"])
        raw_4h = slice_newest_first(candle_set.rows["4H"], candle_set.ts["4H"], "4H", close_ms, CONFIG["slice_4h"])
        raw_5m = slice_newest_first(candle_set.rows["5m"], candle_set.ts["5m"], "5m", close_ms, CONFIG["slice_5m"])
        if (
            len(raw_15m) < CONFIG["min_replay_15m"]
            or len(raw_1h) < CONFIG["min_replay_1h"]
            or len(raw_4h) < CONFIG["min_replay_4h"]
            or len(raw_5m) < CONFIG["min_replay_5m"]
        ):
            continue
        captured_at = iso_from_ms(close_ms)
        try:
            result = compute_signal(
                candles_15m=raw_15m,
                candles_1h=raw_1h,
                candles_4h=raw_4h,
                candles_5m=raw_5m,
                symbol=symbol.removesuffix("-SWAP"),
                config=strategy_cfg,
                captured_at_iso=captured_at,
            )
        except Exception as exc:
            log(f"{symbol} {captured_at}: compute_signal failed: {exc}")
            continue
        ev = result.engine_vars or {}
        row = {
            "symbol": symbol,
            "open_ms": int(candle[0]),
            "ts": captured_at,
            "regime": result.regime,
            "entry_signal": result.entry_signal,
            "trade_style": result.trade_style or "NO_TRADE",
            "side": result.side,
            "drop_reason": result.drop_reason or "",
            "adx_1h": safe_float(ev.get("adx_1h")),
            "adx_4h": safe_float(ev.get("adx_4h")),
            "vol_ratio_sig": safe_float(ev.get("vol_ratio_sig")),
            "slope_15m": safe_float(result.context.get("slope_15m")),
            "slope_1h": safe_float(result.context.get("slope_1h")),
            "day_position": safe_float(ev.get("day_position")),
            "entry_price": result.entry_price,
            "sl_price": result.sl_price,
            "tp1_price": result.tp1_price,
            "engine_vars": ev,
            "indicators": result.indicators,
        }
        decisions.append(row)
        decisions_by_open[int(candle[0])] = row
    events = detect_tradeable_moves(symbol, rows_15m, decisions_by_open, start_close_ms, end_close_ms)
    for event in events:
        event["start_engine"]["move_type_hint"] = event["move_type"]
        event["diagnostic_reason"] = diagnostic_reason(event["start_engine"], event["direction"])
    return decisions, events


def summarize_decisions(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in decisions:
        buckets[row["regime"]].append(row)
    out = []
    for regime, rows in sorted(buckets.items()):
        entries = [r for r in rows if r["entry_signal"] == "ENTRY"]
        styles = Counter(r["trade_style"] for r in entries)
        out.append(
            {
                "regime": regime,
                "n": len(rows),
                "entry": len(entries),
                "entry_rate": pct(len(entries), len(rows)),
                "wait": sum(1 for r in rows if r["entry_signal"] == "WAIT"),
                "no_trade": sum(1 for r in rows if r["entry_signal"] == "NO_TRADE"),
                "fast_entries": styles["FAST"],
                "swing_entries": styles["SWING"],
                "avg_vol": average([r["vol_ratio_sig"] for r in rows]),
                "avg_adx": average([r["adx_1h"] for r in rows]),
            }
        )
    return out


def summarize_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        buckets[(event["regime"], event["move_type"])].append(event)
    out = []
    for (regime, move_type), rows in sorted(buckets.items()):
        caught = sum(1 for r in rows if r["coverage"] == "caught")
        missed = [r for r in rows if r["coverage"] != "caught"]
        reasons = Counter(r["diagnostic_reason"] for r in missed)
        out.append(
            {
                "regime": regime,
                "move_type": move_type,
                "n": len(rows),
                "caught": caught,
                "recall": pct(caught, len(rows)),
                "wait": sum(1 for r in rows if r["coverage"] == "wait"),
                "wrong_side": sum(1 for r in rows if r["coverage"] == "entry_wrong_side"),
                "missed": sum(1 for r in rows if r["coverage"] == "missed"),
                "avg_move_pct": average([r["move_pct"] for r in rows]),
                "p50_peak_bars": median([r["peak_bars_15m"] for r in rows]) if rows else None,
                "top_reasons": reasons.most_common(5),
            }
        )
    return out


def live_signal_benchmark(start_close_ms: int, end_close_ms: int) -> dict[str, Any]:
    signals = {row["id"]: row for row in load_jsonl(SIGNALS_PATH)}
    labels = {row["signal_id"]: row for row in load_jsonl(LABELS_PATH) if row.get("valid") is True}
    rows = []
    for signal_id, label in labels.items():
        signal = signals.get(signal_id)
        if not signal:
            continue
        ts = ms_from_iso(signal["ts"])
        if start_close_ms <= ts <= end_close_ms:
            rows.append({**signal, "outcome": label.get("outcome")})
    by_cell: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_cell[(row.get("regime") or "UNKNOWN", row.get("trade_style") or "UNKNOWN")].append(row)
    out = {}
    for key, vals in sorted(by_cell.items()):
        outcomes = Counter(v["outcome"] for v in vals)
        out[f"{key[0]}|{key[1]}"] = {
            "n": len(vals),
            "tp": outcomes["TP1"] + outcomes["TP2"],
            "sl": outcomes["SL"],
            "time": outcomes["TIME"],
            "wr_decisive": pct(outcomes["TP1"] + outcomes["TP2"], outcomes["TP1"] + outcomes["TP2"] + outcomes["SL"]),
            "time_rate": pct(outcomes["TIME"], len(vals)),
        }
    drift_fast_tp = [
        r for r in rows
        if r.get("regime") == "DRIFT"
        and r.get("trade_style") == "FAST"
        and r.get("outcome") in {"TP1", "TP2"}
    ]
    return {
        "rows": len(rows),
        "by_cell": out,
        "drift_fast_tp_features": {
            "n": len(drift_fast_tp),
            "avg_vol": average([safe_float(r.get("vol_ratio")) for r in drift_fast_tp]),
            "avg_adx": average([safe_float(r.get("adx_1h")) for r in drift_fast_tp]),
        },
    }


def feature_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "n": len(rows),
        "avg_vol": average([safe_float(r.get("vol_ratio_sig") or r.get("engine", {}).get("vol_ratio_sig")) for r in rows]),
        "avg_adx": average([safe_float(r.get("adx_1h") or r.get("engine", {}).get("adx_1h")) for r in rows]),
        "avg_slope15": average([safe_float(r.get("slope_15m") or r.get("engine", {}).get("slope_15m")) for r in rows]),
        "avg_day_pos": average([safe_float(r.get("day_position") or r.get("engine", {}).get("day_position")) for r in rows]),
    }


def render_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" if i == 0 else "---:" for i in range(len(headers))) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(v) for v in row) + " |")
    return lines


def render_report(
    universe: list[str],
    loaded_symbols: list[str],
    skipped_symbols: list[str],
    start_close_ms: int,
    end_close_ms: int,
    decisions: list[dict[str, Any]],
    events: list[dict[str, Any]],
    live: dict[str, Any],
) -> str:
    decision_summary = summarize_decisions(decisions)
    event_summary = summarize_events(events)
    active_symbols = sorted({row["symbol"] for row in decisions})
    zero_decision_symbols = sorted(set(loaded_symbols) - set(active_symbols))
    decision_rows = [
        [
            r["regime"],
            r["n"],
            r["entry"],
            fmt(r["entry_rate"], "%"),
            r["fast_entries"],
            r["swing_entries"],
            r["wait"],
            r["no_trade"],
            fmt(r["avg_vol"], ""),
            fmt(r["avg_adx"], ""),
        ]
        for r in decision_summary
    ]
    event_rows = [
        [
            r["regime"],
            r["move_type"],
            r["n"],
            r["caught"],
            fmt(r["recall"], "%"),
            r["missed"],
            r["wait"],
            r["wrong_side"],
            fmt(r["avg_move_pct"], "%"),
            fmt(r["p50_peak_bars"], ""),
            "; ".join(f"{name}={count}" for name, count in r["top_reasons"][:3]),
        ]
        for r in event_summary
    ]
    reason_rows = []
    for (regime, move_type), rows in sorted(group_events(events).items()):
        missed = [r for r in rows if r["coverage"] != "caught"]
        for reason, count in Counter(r["diagnostic_reason"] for r in missed).most_common(6):
            reason_rows.append([regime, move_type, reason, count, fmt(pct(count, len(missed)), "%")])

    live_rows = []
    for cell, row in live["by_cell"].items():
        live_rows.append([cell, row["n"], row["tp"], row["sl"], row["time"], fmt(row["wr_decisive"], "%"), fmt(row["time_rate"], "%")])

    trend_missed = [e for e in events if e["regime"] == "TRENDING" and e["coverage"] != "caught"]
    ranging_missed = [e for e in events if e["regime"] == "RANGING" and e["coverage"] != "caught"]
    bench_display = []
    live_drift = live.get("drift_fast_tp_features", {})
    bench_display.append(
        [
            "DRIFT FAST live TP signals",
            live_drift.get("n", 0),
            fmt(live_drift.get("avg_vol")),
            fmt(live_drift.get("avg_adx")),
            "n/a",
            "n/a",
        ]
    )
    for name, summary in [
        ("TRENDING missed moves", feature_summary(trend_missed)),
        ("RANGING missed moves", feature_summary(ranging_missed)),
    ]:
        bench_display.append([name, summary["n"], fmt(summary["avg_vol"]), fmt(summary["avg_adx"]), fmt(summary["avg_slope15"]), fmt(summary["avg_day_pos"])])

    recommendations = per_cell_verdicts(event_summary)
    lines = [
        "# Regime Coverage Research - 21.05.2026",
        "",
        f"Replay period: `{iso_from_ms(start_close_ms)}` to `{iso_from_ms(end_close_ms)}` (`{CONFIG['analysis_days']}` days).",
        f"Universe requested: `{len(universe)}` symbols; MTF loaded: `{len(loaded_symbols)}`; decision-active: `{len(active_symbols)}`; skipped: `{len(skipped_symbols)}`.",
        "",
        "This replay imports the real `src.strategy.signal_engine.compute_signal`. Funding, OI, order book, recent trades, and index-candle divergence are not reconstructed, so those fields are neutral/empty. The WS prefilter/cooldown/context gate are not replayed; this is engine recall at 15m closes.",
        "",
    ]
    if zero_decision_symbols:
        lines.append(f"Loaded but no replay decisions due insufficient warmup in the selected window: `{', '.join(zero_decision_symbols)}`.")
        lines.append("")
    if skipped_symbols:
        lines.append(f"Skipped symbols: `{', '.join(skipped_symbols)}`.")
        lines.append("")
    lines.extend(["## Replay Decision Stream", ""])
    lines.extend(render_table(["regime", "n", "ENTRY", "ENTRY %", "FAST entries", "SWING entries", "WAIT", "NO_TRADE", "avg vol", "avg ADX"], decision_rows))
    lines.extend(["", "## Tradeable Movement Recall", ""])
    lines.extend(render_table(["regime", "type", "moves", "caught", "recall", "missed", "WAIT", "wrong side", "avg move", "p50 peak bars", "top miss reasons"], event_rows))
    lines.extend(["", "## Top Silence Reasons On Missed Moves", ""])
    lines.extend(render_table(["regime", "type", "reason", "count", "share"], reason_rows))
    lines.extend(["", "## Live Signal Check In Replay Window", ""])
    lines.extend(render_table(["cell", "n", "TP", "SL", "TIME", "decisive WR", "TIME"], live_rows))
    lines.extend(["", "## Benchmark Vs DRIFT x FAST", ""])
    lines.extend(render_table(["bucket", "n", "avg vol", "avg ADX", "avg slope15", "avg day pos"], bench_display))
    lines.extend(["", "## What Each Cell Needs", ""])
    lines.extend([f"- {line}" for line in recommendations])
    lines.extend(
        [
            "",
            "## GPT Hypotheses",
            "",
            "- `conditions_not_met` is the dominant silence bucket, so the post-hoc diagnostic split is more useful than raw `drop_reason` alone.",
            "- TRENDING misses are mostly not a lack of movement; they are usually alignment/rising-ADX/volume/slope failures at the moment the move starts.",
            "- RANGING movement exists, but the engine's ranging definition is intentionally narrow: BB corridor, falling ADX, day-position edge, and side-vs-VWAP all have to line up.",
            "- If the trader marks missed PNGs as genuinely tradeable, the next phase should model a separate early-move detector per regime instead of loosening all filters globally.",
            "",
            "## Caveats",
            "",
            "- The independent movement detector is deliberately simple: it labels a move as FAST if the peak is inside 4 closed 15m bars and SWING if it is inside 16 bars; adverse excursion must stay below 75% of favorable excursion.",
            "- This is recall research, not PnL backtest. A caught movement means engine side matched the movement start within a small near-start window; it does not guarantee the logged trade would hit TP after fees.",
            "- `BB_FADE` 5m branch is not replayed here; the matrix requested DRIFT/TRENDING/RANGING x FAST/SWING, and the main `compute_signal` 15m decision stream is the source.",
        ]
    )
    return "\n".join(lines) + "\n"


def group_events(events: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[(event["regime"], event["move_type"])].append(event)
    return grouped


def per_cell_verdicts(event_summary: list[dict[str, Any]]) -> list[str]:
    out = []
    by_key = {(r["regime"], r["move_type"]): r for r in event_summary}
    for regime in ["DRIFT", "TRENDING", "RANGING"]:
        for move_type in ["FAST", "SWING"]:
            row = by_key.get((regime, move_type))
            if not row:
                out.append(f"{regime} x {move_type}: no usable movement sample in this replay window.")
                continue
            sample = "small sample" if row["n"] < 20 else "usable sample"
            if row["recall"] >= 45:
                out.append(f"{regime} x {move_type}: engine already covers part of the cell ({fmt(row['recall'], '%')} recall, {sample}); improve precision/entry timing before loosening filters.")
            elif row["recall"] >= 20:
                out.append(f"{regime} x {move_type}: partial coverage ({fmt(row['recall'], '%')} recall, {sample}); inspect top silence reasons before changing thresholds.")
            else:
                reasons = ", ".join(f"{name}={count}" for name, count in row["top_reasons"][:2])
                out.append(f"{regime} x {move_type}: mostly silent ({fmt(row['recall'], '%')} recall, {sample}); blockers: {reasons or 'n/a'}.")
    return out


def render_case_png(path: Path, event: dict[str, Any], candles_15m: list[list[Any]]) -> None:
    start_open = int(event["start_open_ms"])
    idx_map = {int(row[0]): i for i, row in enumerate(candles_15m)}
    if start_open not in idx_map:
        return
    idx = idx_map[start_open]
    start = max(0, idx - 12)
    end = min(len(candles_15m), idx + int(event["peak_bars_15m"]) + 14)
    subset = candles_15m[start:end]
    if len(subset) < 8:
        return
    x0 = idx - start
    peak_x = x0 + int(event["peak_bars_15m"])
    fig, ax = plt.subplots(figsize=(12, 6))
    for i, row in enumerate(subset):
        o, h, l, c = map(float, row[1:5])
        color = "#15936b" if c >= o else "#c23b3b"
        ax.vlines(i, l, h, color=color, linewidth=0.9)
        body_h = max(abs(c - o), (h - l) * 0.02)
        ax.add_patch(patches.Rectangle((i - 0.35, min(o, c)), 0.7, body_h, color=color, alpha=0.78))
    ax.axvline(x0, color="#1f4e99", linestyle=":", linewidth=1.1, label="engine decision")
    ax.scatter([x0], [event["entry_price"]], color="#0b5bd3", s=42, zorder=8)
    ax.annotate(
        f"{event['move_type']} {event['direction']} {fmt(event['move_pct'], '%')}",
        xy=(peak_x, event["peak_price"]),
        xytext=(x0, event["entry_price"]),
        arrowprops={"arrowstyle": "->", "color": "#6f42c1", "lw": 1.2},
        color="#6f42c1",
        fontsize=8,
    )
    title = (
        f"{event['regime']} {event['move_type']} missed | {event['symbol']} "
        f"{event['start_ts']} | {event['diagnostic_reason']}"
    )
    ax.set_title(title, fontsize=9, loc="left")
    step = max(1, len(subset) // 8)
    ticks = list(range(0, len(subset), step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([iso_from_ms(int(subset[i][0]) + TF_MS["15m"])[11:16] for i in ticks], fontsize=8)
    ax.grid(alpha=0.18)
    ax.legend(loc="best", fontsize=8)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def generate_cases(events: list[dict[str, Any]], candle_sets: dict[str, CandleSet]) -> None:
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    for old in CASE_DIR.glob(f"*_{SUFFIX}.png"):
        old.unlink()
    target_regimes = {"DRIFT", "TRENDING", "RANGING"}
    missed = [e for e in events if e["coverage"] != "caught" and e["regime"] in target_regimes]
    for regime in ["DRIFT", "TRENDING", "RANGING"]:
        rows = sorted(
            [e for e in missed if e["regime"] == regime],
            key=lambda e: (e["coverage"] != "missed", -safe_float(e["move_pct"])),
        )[: CONFIG["case_limit_per_regime"]]
        for idx, event in enumerate(rows, start=1):
            candle_set = candle_sets.get(event["symbol"])
            if not candle_set:
                continue
            safe_ts = event["start_ts"].replace(":", "").replace("-", "").replace("Z", "")
            path = CASE_DIR / f"{regime.lower()}_{idx:02d}_{event['symbol']}_{event['move_type']}_{safe_ts}_{SUFFIX}.png"
            render_case_png(path, event, candle_set.rows["15m"])


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_text("", encoding="utf-8")
    universe = load_universe()
    strategy_cfg = load_strategy_config()
    start_close_ms, end_close_ms = cached_reference_window(universe)
    log(f"replay window {iso_from_ms(start_close_ms)} -> {iso_from_ms(end_close_ms)}")
    log(f"universe symbols: {len(universe)}")

    candle_sets: dict[str, CandleSet] = {}
    skipped_symbols = []
    all_decisions: list[dict[str, Any]] = []
    all_events: list[dict[str, Any]] = []
    for symbol in universe:
        candle_set = load_symbol_candles(symbol, start_close_ms, end_close_ms)
        if candle_set is None:
            skipped_symbols.append(symbol)
            log(f"{symbol}: skipped (missing MTF candles)")
            continue
        candle_sets[symbol] = candle_set
        log(f"{symbol}: loaded {candle_set.source}")
        decisions, events = replay_symbol(symbol, candle_set, strategy_cfg, start_close_ms, end_close_ms)
        all_decisions.extend(decisions)
        all_events.extend(events)
        log(f"{symbol}: decisions={len(decisions)} moves={len(events)}")

    live = live_signal_benchmark(start_close_ms, end_close_ms)
    REPORT_MD.write_text(
        render_report(
            universe,
            sorted(candle_sets),
            skipped_symbols,
            start_close_ms,
            end_close_ms,
            all_decisions,
            all_events,
            live,
        ),
        encoding="utf-8",
    )
    summary = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "config": CONFIG,
        "period": {"start": iso_from_ms(start_close_ms), "end": iso_from_ms(end_close_ms)},
        "universe_count": len(universe),
        "loaded_symbols": sorted(candle_sets),
        "skipped_symbols": skipped_symbols,
        "decision_summary": summarize_decisions(all_decisions),
        "event_summary": summarize_events(all_events),
        "live_signal_benchmark": live,
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    generate_cases(all_events, candle_sets)
    log(f"saved {REPORT_MD}")
    log(f"saved {SUMMARY_JSON}")
    log(f"saved cases to {CASE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
